# hledac/universal/fetching/public_fetcher.py
# Sprint 8AD — First live public text fetch adapter v1
# aiohttp/shared-session, chunked size-safe, timeout-safe, passive-only
"""
Public-passive text/HTML fetcher using shared aiohttp session runtime.
Always-on, bounded, fail-soft, typed via msgspec.Struct.

P4: Tor + stealth layer integration:
- .onion domains routed via Tor SOCKS5 proxy (9050)
- Optional stealth mode via StealthManager
- Circuit renewal every TOR_CIRCUIT_RENEWAL_REQUEST_COUNT requests
- Random jitter before each request when using Tor/stealth
"""
from __future__ import annotations

import atexit
import asyncio
import logging
import random
import re
import time
import urllib.parse
from typing import Final, Optional

import psutil

# Sprint F206AL: Import canonical M1 8GB threshold from uma_budget.
from hledac.universal.utils.uma_budget import M1_FETCH_SOFT_CEILING_GB

import aiohttp

import msgspec

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.patterns.pattern_matcher import match_text
from hledac.universal.transport.circuit_breaker import (
    get_breaker,
    CircuitBreaker,
    CircuitDecision,
)
from hledac.universal.transport.httpx_transport import (
    should_use_httpx_h2,
    fetch_via_httpx_h2,
)
from hledac.universal.transport.curl_cffi_transport import should_use_curl_cffi
from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi
from hledac.universal.utils.concurrency import (
    FETCH_SEMAPHORE,
    get_clearnet_semaphore,
    get_tor_semaphore,
    get_fetch_semaphore,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# P4: Tor + stealth constants
# ---------------------------------------------------------------------------
TOR_SOCKS_PROXY: Final[str] = "socks5://127.0.0.1:9050"
TOR_CIRCUIT_RENEWAL_REQUEST_COUNT: Final[int] = 10
TOR_STEALTH_TIMEOUT_SCALE: Final[float] = 2.0  # Tor requests need longer timeouts
JITTER_MIN_S: Final[float] = 0.1
JITTER_MAX_S: Final[float] = 0.5

# Module-level state for Tor session management
_tor_session: Optional["aiohttp.ClientSession"] = None
_tor_request_count: int = 0
_tor_session_lock: "asyncio.Lock" = asyncio.Lock()

# P10: Module-level state for I2P session management
_i2p_session: Optional["aiohttp.ClientSession"] = None

# F206AT: Public fetcher pool authority verdict.
# Tor and I2P sessions are LOCAL FALLBACK pools managed directly by public_fetcher.
# They are NOT coordinated through FetchCoordinator transport policy.
# When a canonical transport provider is injected, it supersedes these local pools.
PUBLIC_FETCHER_POOL_AUTHORITY: Final[str] = "local_fallback_until_transport_unified"

# F206AT: Optional injected session provider seam.
# When set (via constructor or param), used instead of local _tor_session/_i2p_session.
# Format: tuple of (tor_session, i2p_session) or None
_injected_session_provider: Optional[
    tuple["aiohttp.ClientSession | None", "aiohttp.ClientSession | None"]
] = None

# F206AT: Session source telemetry — truth about where sessions come from.
# Updated on each _get_tor_session / _get_i2p_session call.
_session_source_telemetry: dict[str, str] = {
    "tor": "unavailable",
    "i2p": "unavailable",
}


def inject_session_provider(
    tor_session: "aiohttp.ClientSession | None",
    i2p_session: "aiohttp.ClientSession | None",
) -> None:
    """F206AT: Inject canonical session provider for Tor/I2P pools.

    When injected with non-None sessions, the provided sessions are used instead of
    local _tor_session/_i2p_session. This allows FetchCoordinator or transport layer
    to own the canonical session lifecycle.

    Calling with (None, None) resets to local-only mode — the seam is deactivated.

    Args:
        tor_session: Canonical Tor aiohttp session, or None to use local fallback.
        i2p_session: Canonical I2P aiohttp session, or None to use local fallback.
    """
    global _injected_session_provider
    # Deactivate seam if both are None — reset to local pools
    if tor_session is None and i2p_session is None:
        _injected_session_provider = None
    else:
        _injected_session_provider = (tor_session, i2p_session)


def get_session_source_telemetry() -> dict[str, str]:
    """F206AT: Return snapshot of session source telemetry.

    Returns:
        dict with keys:
        - tor: "injected" | "local_tor" | "unavailable"
        - i2p: "injected" | "local_i2p" | "unavailable"
        - transport_policy_bypassed: "true" | "false"
        - fallback_reason: str | None
    """
    global _session_source_telemetry
    result = dict(_session_source_telemetry)
    result["transport_policy_bypassed"] = (
        "true" if _injected_session_provider is None else "false"
    )
    result["fallback_reason"] = (
        "injected_provider_available"
        if _injected_session_provider is not None
        else "local_pool_until_transport_unified"
    )
    return result

# P7: Camoufox singleton lock — max 1 instance across entire fetcher
_CAMOUFOX_LOCK: "asyncio.Lock" = asyncio.Lock()

# ---------------------------------------------------------------------------
# Public API — single entry point
# ---------------------------------------------------------------------------

DEFAULT_UA: Final[str] = (
    "Mozilla/5.0 (compatible; research-bot/1.0; +passive-public-fetch)"
)

MAX_BYTES_DEFAULT: Final[int] = 2_000_000
MAX_BYTES_HARD: Final[int] = 10_000_000

# ---------------------------------------------------------------------------
# Typed result DTO
# ---------------------------------------------------------------------------

# Transport counters — per-fetch, M1-safe slots-based dataclass
_MAX_COUNT: int = 999_999


class TransportCounters:
    """Lightweight per-fetch transport counter bundle (M1-safe __slots__).

    Bounded ints — counters saturate at MAX_COUNT rather than growing unbounded.
    Not exposed in public API — aggregated by sprint coordinator from FetchResult.
    """

    __slots__ = (
        "aiohttp_count",
        "httpx_h2_count",
        "curl_cffi_count",
        "tor_aiohttp_socks_count",
        "i2p_aiohttp_socks_count",
        "js_renderer_count",
        "fallback_count",
        "curl_cffi_fallback_to_aiohttp_count",
        "httpx_h2_fallback_to_aiohttp_count",
    )

    def __init__(
        self,
        aiohttp_count: int = 0,
        httpx_h2_count: int = 0,
        curl_cffi_count: int = 0,
        tor_aiohttp_socks_count: int = 0,
        i2p_aiohttp_socks_count: int = 0,
        js_renderer_count: int = 0,
        fallback_count: int = 0,
        curl_cffi_fallback_to_aiohttp_count: int = 0,
        httpx_h2_fallback_to_aiohttp_count: int = 0,
    ) -> None:
        self.aiohttp_count = min(aiohttp_count, _MAX_COUNT)
        self.httpx_h2_count = min(httpx_h2_count, _MAX_COUNT)
        self.curl_cffi_count = min(curl_cffi_count, _MAX_COUNT)
        self.tor_aiohttp_socks_count = min(tor_aiohttp_socks_count, _MAX_COUNT)
        self.i2p_aiohttp_socks_count = min(i2p_aiohttp_socks_count, _MAX_COUNT)
        self.js_renderer_count = min(js_renderer_count, _MAX_COUNT)
        self.fallback_count = min(fallback_count, _MAX_COUNT)
        self.curl_cffi_fallback_to_aiohttp_count = min(curl_cffi_fallback_to_aiohttp_count, _MAX_COUNT)
        self.httpx_h2_fallback_to_aiohttp_count = min(httpx_h2_fallback_to_aiohttp_count, _MAX_COUNT)


class FetchResult(msgspec.Struct, frozen=True, gc=False):
    """Frozen msgspec result — no mutations after construction.

    Backward-compatible: added fields have defaults so existing callers are unaffected.

    Access-path truth fields (F169B):
    - redirected: True when final_url != url (explicit redirect flag, downstream-friendly)
    - redirect_target: redirect destination (set only when redirected=True)
    - failure_stage: coarse classification of where fetch pipeline failed
    - network_error_kind: fine-grained network error kind for connection/tls/dns failures
    """

    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str | None
    fetched_bytes: int  # actual bytes read
    declared_length: int  # Content-Length header value, -1 if absent
    elapsed_ms: float
    error: str | None = None
    # Added in F164A — feed ingress hardening
    xml_recovered: bool = False  # True: body was XML-ish but Content-Type was wrong, body is now text
    xml_source_hint: bool = False  # F178E: True when xml_recovered=True — downstream can detect XML origin
    decode_replaced: bool = False  # True: UTF-8 decode used replacement chars
    decode_replacement_count: int = 0  # F178E: actual count of U+FFFD replacement chars inserted
    body_read_error: bool = False  # True: headers were OK but body stream failed mid-read
    # Added in F169B — access-path truth hardening
    redirected: bool = False  # True: final_url != url (explicit redirect signal)
    redirect_target: str | None = None  # redirect destination (set only when redirected=True)
    failure_stage: str | None = None  # validation | connection | tls | http | body | size
    network_error_kind: str | None = None  # dns_error | connect_error | tls_error | timeout
    # Added in F206K — Transport Capability Layer 2026 telemetry
    selected_transport: str | None = None  # aiohttp | httpx_h2 | aiohttp_socks | stealth | js
    http_version: str | None = None  # h2 | http/1.1 | h2c (detected post-response)
    transport_policy_reason: str | None = None  # api_like | darknet_url | stealth_required | js_required | clearnet_default | httpx_h2_disabled_env | httpx_h2_disabled | httpx_h2_fallback | freenet_not_httpx_supported | explicit_stealth | status_403_or_429 | protection_detected | default_aiohttp
    transport_fallback_reason: str | None = None  # set when fallback occurred (curl_cffi_failed:..., httpx_h2_fallback)
    # Added in F206N — Transport Telemetry Counters
    transport_counters: "TransportCounters | None" = None
    # Added in F207F — PUBLIC Yield: why JS renderer was skipped
    js_renderer_skipped_reason: str | None = None  # xml_or_feed_url | xml_recovered | browser_unavailable


# ---------------------------------------------------------------------------
# Content-type whitelist (text-ish only)
# ---------------------------------------------------------------------------

ACCEPTED_CONTENT_TYPES: Final[frozenset[str]] = frozenset({
    "text/html",
    "text/plain",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
})


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str | None:
    """
    Validate URL is http/https and well-formed.
    Returns None on success, error string on failure.
    """
    if not url or not isinstance(url, str):
        return "url_empty"
    url = url.strip()
    if not url:
        return "url_empty"
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, AttributeError) as e:
        logger.warning("URL parse error for %s: %s", url, e)
        return "url_malformed"
    scheme = parsed.scheme.lower()
    if not scheme:
        return "url_malformed"
    if scheme not in ("http", "https"):
        return f"url_unsupported_scheme:{scheme}"
    if not parsed.netloc:
        return "url_no_netloc"
    return None


# ---------------------------------------------------------------------------
# Retry constants — bounded, M1-safe
# ---------------------------------------------------------------------------

MAX_RETRIES: Final[int] = 1  # exactly one retry; no infinite loops
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504, 520})


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES


def _extract_retry_after(headers) -> float | None:
    """Parse Retry-After header, return seconds or None."""
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if ra is None:
        return None
    try:
        return float(ra)
    except (ValueError, TypeError):
        return None


def _compute_backoff_seconds(retry_after: float | None, attempt: int) -> float:
    """Return bounded backoff in seconds.

    Uses Retry-After if available, otherwise exponential backoff capped at 8 s.
    Attempt 0 = no backoff (first failure already counted).
    """
    if retry_after is not None and retry_after > 0:
        return min(retry_after, 60.0)  # cap at 60 s to bound pause
    return min(2.0 ** (attempt + 1), 8.0)  # 4 s, capped at 8 s


def _build_retry_error(status_code: int, retry_after: float | None) -> str:
    """Build retry error string with : separator between code and details.

    Adapter uses .split(":", 2) — first two parts are always prefix+code,
    any additional colons in the message body are preserved in part[2].
    """
    parts = [f"retryable:{status_code}"]
    if retry_after is not None:
        parts.append(f"retry_after={retry_after:.1f}s")
    else:
        parts.append("backoff=exp")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# F169B: Access-path truth helpers — derive-only, no new transport
# ---------------------------------------------------------------------------


def _derive_redirect_fields(url: str, final_url: str) -> tuple[bool, str | None]:
    """Return (redirected, redirect_target) based on URL comparison.

    downstream can use redirected=True as explicit signal instead of
    computing final_url != url themselves.
    """
    if final_url != url:
        return (True, final_url)
    return (False, None)


def _derive_failure_stage_and_network_kind(error: str | None) -> tuple[str | None, str | None]:
    """Parse error string to extract structured failure_stage and network_error_kind.

    Returns (failure_stage, network_error_kind).
    Both are None when error is None (success) or for URL-validation errors.

    failure_stage taxonomy:
      - validation  : URL was invalid before any network call
      - connection  : TCP/DNS/connection-level failure (body never reached)
      - tls          : TLS handshake failure
      - http         : HTTP-level failure (response received, non-2xx)
      - body         : headers OK but body read failed mid-stream
      - size         : body truncated due to size cap

    network_error_kind (connection/tls only):
      - dns_error    : DNS resolution failure
      - connect_error: TCP connection refused/reset
      - tls_error    : TLS handshake/verification failure
      - timeout      : request timed out
    """
    if error is None:
        return (None, None)

    # URL validation errors — pre-connection, network_error_kind stays None
    if error.startswith("url_"):
        return ("validation", None)

    # Timeout — explicit in code, no ambiguity
    if error == "timeout":
        return ("connection", "timeout")

    # Size cap — structured, no network error kind
    if error == "size_cap_exceeded":
        return ("size", None)

    # content_type_rejected — HTTP response but content unacceptable
    if error.startswith("content_type_rejected:"):
        return ("http", None)

    # retryable status codes — HTTP-level
    if error.startswith("retryable:"):
        return ("http", None)

    # Generic fetch_error; prefix — connection/tls level
    if error.startswith("fetch_error;"):
        # Format: "fetch_error;ExceptionType;message"
        parts = error.split(";", 2)
        exc_type = parts[1] if len(parts) > 1 else ""

        # TLS variants
        if "SSL" in exc_type or "TLS" in exc_type or "Certificate" in exc_type:
            return ("tls", "tls_error")
        # DNS
        if "DNS" in exc_type or "Resolver" in exc_type:
            return ("connection", "dns_error")
        # Connection (refused, reset, connect timeout)
        if "Connect" in exc_type or "Connection" in exc_type or "Network" in exc_type:
            return ("connection", "connect_error")
        # Default for any other fetch_error: connection-level unknown
        return ("connection", "connect_error")

    # Unknown error format — body-level if we got here without a clear stage
    return ("body", None)


# Sprint F206AC: Fetch error taxonomy for public_branch_verdict telemetry
_FETCH_ERROR_TAXONOMY: dict[str, str] = {
    "dns_error": "dns_error",
    "connect_error": "connect_error",
    "tls_error": "tls_error",
    "timeout": "read_timeout",
    "content_type_rejected:": "content_type_rejected",
    "fetch_text_none_or_empty": "body_empty",
    "fetch_timeout_after_": "connect_timeout",
    "fetch_exception: asyncio.TimeoutError": "connect_timeout",
    "fetch_exception: TimeoutError": "read_timeout",
    "fetch_exception: ClientConnectorError": "connect_error",
    "fetch_exception: ClientSSLError": "tls_error",
    "fetch_exception: ClientProxyError": "proxy_error",
    "fetch_exception: ClientConnectorCertificateError": "tls_error",
    "circuit_breaker": "circuit_breaker_blocked",
    "resource_governor": "resource_governor_blocked",
}


def classify_fetch_error(result_or_error) -> str:
    """Classify a fetch outcome into a flat error type string for verdict telemetry.

    Takes a FetchResult (success or failure) or an error string.
    Returns one of the Sprint F206AC taxonomy strings:
      none | dns_error | connect_timeout | read_timeout | tls_error | proxy_error
      | http_403 | http_404 | http_429 | http_5xx | content_type_rejected
      | body_empty | max_bytes_exceeded | circuit_breaker_blocked
      | resource_governor_blocked | task_cancelled | unknown_fetch_error

    HARD RULE: CancelledError is re-raised, never classified and swallowed.
    """
    # ---- Handle FetchResult objects ----------------------------------------
    if hasattr(result_or_error, "status_code"):
        result = result_or_error
        # Success path
        if result.error is None and result.status_code == 200 and result.text:
            # Check for body_empty (success but no text)
            if not result.text.strip():
                return "body_empty"
            return "none"
        # Error path from FetchResult
        error_str = result.error or ""
        status_code = result.status_code or 0
        failure_stage = getattr(result, "failure_stage", None) or ""
        network_kind = getattr(result, "network_error_kind", None) or ""

        # CancelledError — re-raise
        if "CancelledError" in error_str:
            import asyncio
            raise asyncio.CancelledError("fetch cancelled")

        # HTTP status codes (only when we got a response)
        if status_code == 403:
            return "http_403"
        if status_code == 404:
            return "http_404"
        if status_code == 429:
            return "http_429"
        if 500 <= status_code < 600:
            return "http_5xx"

        # Structural failures from failure_stage / network_error_kind
        if failure_stage == "validation":
            return "unknown_fetch_error"
        if failure_stage == "tls" or network_kind == "tls_error":
            return "tls_error"
        if network_kind == "dns_error":
            return "dns_error"
        if network_kind == "connect_error":
            return "connect_error"
        if network_kind == "timeout":
            return "read_timeout"
        if failure_stage == "http":
            if "content_type_rejected" in error_str:
                return "content_type_rejected"
            return "unknown_fetch_error"
        if failure_stage == "size":
            return "max_bytes_exceeded"

        # Circuit/resource blocks
        if "circuit_breaker" in error_str:
            return "circuit_breaker_blocked"
        if "resource_governor" in error_str:
            return "resource_governor_blocked"

        # Exception-type-based classification
        for prefix, category in (
            ("fetch_exception: asyncio.TimeoutError", "connect_timeout"),
            ("fetch_exception: TimeoutError", "read_timeout"),
            ("fetch_exception: ClientConnectorError", "connect_error"),
            ("fetch_exception: ClientSSLError", "tls_error"),
            ("fetch_exception: ClientProxyError", "proxy_error"),
            ("fetch_exception: ClientConnectorCertificateError", "tls_error"),
            ("fetch_timeout_after_", "connect_timeout"),
            ("fetch_text_none_or_empty", "body_empty"),
            ("content_type_rejected:", "content_type_rejected"),
        ):
            if error_str.startswith(prefix):
                return category

        if error_str:
            return "unknown_fetch_error"
        return "none"

    # ---- Handle plain error strings ---------------------------------------
    error_str = str(result_or_error) if result_or_error is not None else ""

    # CancelledError — re-raise
    if "CancelledError" in error_str:
        import asyncio
        raise asyncio.CancelledError("fetch cancelled")

    if not error_str:
        return "none"

    for prefix, category in _FETCH_ERROR_TAXONOMY.items():
        if error_str.startswith(prefix):
            return category

    return "unknown_fetch_error"


# ---------------------------------------------------------------------------
# XML-ish body sniffing helper — bounded, fail-safe
# ---------------------------------------------------------------------------

_XML_MARKER = b"<?xml"
_XML_TAG_RE = re.compile(rb"^\s*<[a-zA-Z]", re.IGNORECASE)


def _looks_xmlish(body: bytes) -> bool:
    """Return True if body starts like XML (<?xml or <tag).

    Strips leading ASCII whitespace so servers that prepend newlines
    before the XML declaration are correctly identified.
    """
    stripped = body.lstrip()
    if stripped.startswith(_XML_MARKER):
        return True
    return bool(_XML_TAG_RE.match(stripped))


# ---------------------------------------------------------------------------
# Decode helper — fail-soft, truth-bearing
# ---------------------------------------------------------------------------

def _try_decode(body: bytes) -> tuple[str, bool, int]:
    """Decode bytes to str, return (text, replaced_bool, replacement_count).

    F178E: replacement_count is actual U+FFFD count (not just bool).
    Charset fallback: try UTF-8 → Windows-1252 → Latin-1 before replace.

    replaced_bool=True when UTF-8 decoder used replacement chars (U+FFFD).
    This tells the adapter that the body was garbled, not truly empty.
    """
    # Try strict UTF-8 first
    try:
        text = body.decode("utf-8", errors="strict")
        return (text, False, 0)
    except UnicodeDecodeError:
        pass

    # F178E: Windows-1252 fallback (common in legacy Western feeds)
    try:
        text = body.decode("windows-1252", errors="strict")
        return (text, False, 0)
    except (UnicodeDecodeError, LookupError):
        pass

    # Latin-1 fallback (always succeeds — byte 0-255 maps 1:1)
    try:
        text = body.decode("latin-1", errors="strict")
        return (text, True, 0)  # lossy but usable
    except (UnicodeDecodeError, LookupError):
        pass

    # Final fallback: UTF-8 replace mode — count actual replacements
    text = body.decode("utf-8", errors="replace")
    count = text.count("\ufffd")
    return (text, True, count)


# ---------------------------------------------------------------------------
# P4: Tor session helpers — SOCKS5 proxy via aiohttp_socks
# ---------------------------------------------------------------------------


def _is_onion_url(url: str) -> bool:
    """Detect if URL targets a .onion darknet address."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname.lower().endswith(".onion") if parsed.hostname else False
    except (ValueError, AttributeError) as e:
        logger.warning("URL parse error in _is_onion_url for %s: %s", url, e)
        return False


def _is_i2p_url(url: str) -> bool:
    """
    P10: Detect if URL targets an I2P address (.i2p or .b32.i2p).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        return hostname.endswith(".i2p") or hostname.endswith(".b32.i2p")
    except (ValueError, AttributeError) as e:
        logger.warning("URL parse error in _is_i2p_url for %s: %s", url, e)
        return False


def _is_freenet_url(url: str) -> bool:
    """
    P10: Detect if URL targets a Freenet address (.freenet).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname.lower().endswith(".freenet") if parsed.hostname else False
    except (ValueError, AttributeError) as e:
        logger.warning("URL parse error in _is_freenet_url for %s: %s", url, e)
        return False


async def _get_tor_session() -> "aiohttp.ClientSession":
    """Get or create aiohttp session via Tor SOCKS5 proxy (lazy, singleton).

    F206AT: If _injected_session_provider is set, uses the injected Tor session
    and records source as 'injected'. Otherwise uses local _tor_session and
    records source as 'local_tor'.
    """
    global _tor_session, _session_source_telemetry
    # F206AT: Check for injected provider first
    if _injected_session_provider is not None:
        injected_tor, _ = _injected_session_provider
        if injected_tor is not None and not injected_tor.closed:
            _session_source_telemetry["tor"] = "injected"
            return injected_tor
    if _tor_session is None or _tor_session.closed:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise RuntimeError("aiohttp_socks required for Tor: pip install aiohttp_socks")
        connector = ProxyConnector.from_url(TOR_SOCKS_PROXY, rdns=True)
        _tor_session = aiohttp.ClientSession(connector=connector)
    _session_source_telemetry["tor"] = "local_tor"
    return _tor_session


async def _get_i2p_session() -> "aiohttp.ClientSession":
    """
    P10: Get or create aiohttp session via I2P SOCKS5 proxy (lazy, singleton).
    Uses aiohttp_socks.ProxyConnector for .i2p/.b32.i2p URLs.

    F206AT: If _injected_session_provider is set, uses the injected I2P session
    and records source as 'injected'. Otherwise uses local _i2p_session and
    records source as 'local_i2p'.
    """
    global _i2p_session, _session_source_telemetry
    # F206AT: Check for injected provider first
    if _injected_session_provider is not None:
        _, injected_i2p = _injected_session_provider
        if injected_i2p is not None and not injected_i2p.closed:
            _session_source_telemetry["i2p"] = "injected"
            return injected_i2p
    if _i2p_session is None or _i2p_session.closed:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise RuntimeError("aiohttp_socks required for I2P: pip install aiohttp_socks")
        # I2P default SOCKS port is 7654
        connector = ProxyConnector.from_url("socks5://127.0.0.1:7654", rdns=True)
        _i2p_session = aiohttp.ClientSession(connector=connector)
    _session_source_telemetry["i2p"] = "local_i2p"
    return _i2p_session


async def _renew_tor_circuit() -> bool:
    """
    Renew Tor circuit via NEWNYM signal through control port.
    Returns True if successful, False otherwise.
    """
    try:
        import stem.control
        with stem.control.Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(stem.control.Signal.NEWNYM)
            logger.debug("Tor circuit renewed via NEWNYM signal")
            return True
    except Exception as e:
        logger.warning(f"Tor circuit renewal failed: {e}")
        return False


async def _maybe_renew_tor_circuit() -> None:
    """Renew Tor circuit if request count threshold reached."""
    global _tor_request_count
    _tor_request_count += 1
    if _tor_request_count >= TOR_CIRCUIT_RENEWAL_REQUEST_COUNT:
        _tor_request_count = 0
        await _renew_tor_circuit()


async def _jitter_delay() -> None:
    """Apply random jitter before request (Tor/stealth anti-correlation)."""
    await asyncio.sleep(random.uniform(JITTER_MIN_S, JITTER_MAX_S))


async def _close_tor_session() -> None:
    """Close the Tor session (for cleanup)."""
    global _tor_session
    if _tor_session is not None and not _tor_session.closed:
        await _tor_session.close()
        _tor_session = None


def _close_tor_session_sync() -> None:
    """Sync wrapper for Tor session cleanup via atexit."""
    global _tor_session
    if _tor_session is not None and not _tor_session.closed:
        try:
            _tor_session.close()
        except Exception as e:
            logger.warning("Error closing Tor session: %s", e)
        _tor_session = None


async def _close_i2p_session() -> None:
    """
    P10: Close the I2P session (for cleanup).
    """
    global _i2p_session
    if _i2p_session is not None and not _i2p_session.closed:
        await _i2p_session.close()
        _i2p_session = None


def _close_i2p_session_sync() -> None:
    """Sync wrapper for I2P session cleanup via atexit."""
    global _i2p_session
    if _i2p_session is not None and not _i2p_session.closed:
        try:
            _i2p_session.close()
        except Exception as e:
            logger.warning("Error closing I2P session: %s", e)
        _i2p_session = None


atexit.register(_close_tor_session_sync)
atexit.register(_close_i2p_session_sync)


# ---------------------------------------------------------------------------
# P7: JS detection and Camoufox/nodriver rendering
# ---------------------------------------------------------------------------

# JS detection patterns — trigger Camoufox retry
_NOSCRIPT_RE = re.compile(r"<noscript[^>]*>|enable javascript", re.IGNORECASE)

# F207F: Feed/RSS URL detection — skip JS renderer for XML-ish feeds
_FEED_URL_RE = re.compile(
    r"/?(?:rss|feed|atom|xml|sitemap|opensearch)",
    re.IGNORECASE,
)

# F207F: Browser unavailable cache — process-level, set once after first failure
_browser_unavailable: bool = False
"""True when both Camoufox and nodriver fail due to missing browser binary."""


def _looks_like_feed_url(url: str) -> bool:
    """Return True if URL path strongly suggests an RSS/XML/Atom/Sitemap feed."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    return bool(_FEED_URL_RE.search(path))


def _needs_js_fetch(text: str) -> bool:
    """Detect if response suggests JS-rendered content is needed."""
    return bool(_NOSCRIPT_RE.search(text))


async def _fetch_with_camoufox(url: str, timeout: float = 15.0) -> str:
    """
    Fetch JS-heavy page via Camoufox (Firefox-based anti-detect).
    Max 1 instance, protected by _CAMOUFOX_LOCK singleton.
    M1-optimized: headless, WebGL spoofed for Apple M1.

    F202H: Uses opsec_policy.get_renderer_policy() for M1 conflict guard —
    replaces inline is_embedding_context_active() check with centralized policy.
    """
    # F202H: Use opsec_policy for M1 model+renderer conflict guard
    try:
        from hledac.universal.embedding_pipeline import is_embedding_context_active
        from hledac.universal.runtime.opsec_policy import get_renderer_policy, OPSECContext

        has_model = is_embedding_context_active()
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)
        if not policy.allowed:
            logger.warning(
                f"[F202H] Renderer blocked by opsec_policy: {policy.blocked_reason} "
                f"— skipping Camoufox for {url}"
            )
            return ""
    except Exception as e:
        logger.warning("Error checking renderer policy, proceeding with caution: %s", e)

    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        logger.debug("camoufox not installed, JS fetch unavailable")
        global _browser_unavailable
        _browser_unavailable = True
        return ""

    async with _CAMOUFOX_LOCK:
        try:
            async with AsyncCamoufox(
                headless=True,
                os="macos",
                webgl_config=("Apple", "Apple M1, or similar"),
                fingerprint_seed=int(time.time()),
            ) as browser:
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                    html = await page.content()
                finally:
                    await page.close()
                return html
        except Exception as e:
            logger.warning(f"Camoufox fetch failed for {url}: {e}")
            return ""


async def _fetch_with_nodriver(url: str) -> str:
    """
    Fallback JS fetch via nodriver (direct CDP, no WebDriver).
    Faster startup than Camoufox, suitable for CDP features.
    """
    try:
        import nodriver as uc
    except ImportError:
        logger.debug("nodriver not installed, CDP fetch unavailable")
        global _browser_unavailable
        _browser_unavailable = True
        return ""

    browser = None
    try:
        browser = await uc.start(
            headless=True,
            browser_args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.get(url)
        await asyncio.sleep(2)  # jitter for bot detection
        html = await page.get_content()
        await page.close()  # CRITICAL: without close() → memory leak
        return html
    except Exception as e:
        logger.warning(f"nodriver fetch failed: {e}")
        return ""
    finally:
        if browser:
            browser.stop()


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------


async def async_fetch_public_text(
    url: str,
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
    use_stealth: bool = False,
    use_js: bool = False,
    use_doh: bool = False,
) -> FetchResult:
    """
    Fetch a public URL using the shared aiohttp session.

    P4 stealth mode: optional StealthManager/StealthSession for enhanced privacy.
    P4 Tor mode: .onion URLs automatically routed via Tor SOCKS5 proxy.
    P7 JS mode: Camoufox (primary) with nodriver fallback for JS-heavy pages.
    Chunked streaming with hard size cap.
    CancelledError propagates (not swallowed).

    Parameters
    ----------
    url : str
        Target URL (http or https only, .onion via Tor SOCKS5).
    timeout_s : float
        Per-request timeout in seconds (default 35 s, scaled x2 for Tor).
    max_bytes : int
        Maximum bytes to read from body (default 2 MB, hard cap 10 MB).
    use_stealth : bool
        If True, use StealthManager/StealthSession for enhanced stealth
        (header rotation, fingerprint randomization, rate limiting).
    use_js : bool
        If True, force JS rendering via Camoufox/nodriver.
    use_doh : bool
        P16: If True, resolve hostname via DoH (cloudflare-dns) before
        connecting. Falls back to system DNS if DoH fails. Configurable
        via hledac.universal.config.PrivacyConfig.use_doh.

    Returns
    -------
    FetchResult
        Typed result with final_url, status, content_type, text (or None),
        byte counts, elapsed_ms, and optional error.
    """
    t0 = time.monotonic()

    # --- F206N: Transport counters (per-fetch, bounded) ---
    _tc = TransportCounters()

    # --- Type guard: non-string input fails fast, fail-soft ---
    if not isinstance(url, str):
        elapsed_ms = (time.monotonic() - t0) * 1000
        return FetchResult(
            url=str(url) if url is not None else "",
            final_url=str(url) if url is not None else "",
            status_code=0,
            content_type="",
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=elapsed_ms,
            error="url_empty",
            failure_stage="validation",
        )

    # --- URL validation (strip happens inside _validate_url) ---
    validation_error = _validate_url(url)
    if validation_error is not None:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type="",
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=elapsed_ms,
            error=validation_error,
            failure_stage="validation",
        )

    # --- F204B: Domain circuit breaker check (fail-soft) ---
    _circuit_breaker_domain: str = ""
    _circuit_breaker: "CircuitBreaker" | None = None
    try:
        parsed_url = urllib.parse.urlparse(url)
        _circuit_breaker_domain = parsed_url.netloc
        if _circuit_breaker_domain:
            _circuit_breaker = get_breaker(_circuit_breaker_domain)
            decision = _circuit_breaker.check_circuit()
            if not decision.allowed:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=0,
                    content_type="",
                    text=None,
                    fetched_bytes=0,
                    declared_length=-1,
                    elapsed_ms=elapsed_ms,
                    error=f"circuit_breaker_open:{decision.state}:{decision.reason}",
                    failure_stage="circuit_breaker",
                    selected_transport="aiohttp",
                    transport_policy_reason="clearnet_default",
                )
    except Exception as e:
        logger.debug(f"Circuit breaker check failed (non-fatal): {e}")

    # --- Size cap enforcement ---
    if max_bytes > MAX_BYTES_HARD:
        max_bytes = MAX_BYTES_HARD

    # --- P7: Explicit JS rendering mode ---
    if use_js:
        logger.info(f"JS rendering requested for {url}")
        js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
        if not js_html:
            logger.warning(f"Camoufox failed, trying nodriver: {url}")
            js_html = await _fetch_with_nodriver(url)
        if js_html:
            js_text, _ = await process_html_payload(js_html, url)
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.js_renderer_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                text=js_text,
                fetched_bytes=len(js_html),
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=None,
                selected_transport="js",
                transport_policy_reason="js_required",
                transport_counters=_tc,
            )
        # JS rendering completely failed
        elapsed_ms = (time.monotonic() - t0) * 1000
        _tc.js_renderer_count += 1
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type="",
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=elapsed_ms,
            error="js_render_failed",
            failure_stage="fetching",
            selected_transport="js",
            transport_policy_reason="js_required",
            transport_counters=_tc,
        )

    # --- P4: Determine transport mode ---
    is_onion = _is_onion_url(url)
    is_i2p = _is_i2p_url(url)
    is_freenet = _is_freenet_url(url)
    use_tor = is_onion  # .onion URLs always go via Tor
    use_i2p = is_i2p  # .i2p/.b32.i2p URLs go via I2P SOCKS
    use_freenet = is_freenet  # .freenet URLs go via Freenet HTTP proxy

    # --- F206K: HTTPX H2 optional clearnet lane ---
    _use_httpx_h2, _httpx_reason = should_use_httpx_h2(url, use_stealth, use_js)
    if _use_httpx_h2:
        logger.debug(f"[HTTPX] H2 lane selected for {url}: {_httpx_reason}")
        try:
            import httpx as _httpx

            _httpx_resp = await fetch_via_httpx_h2(url, timeout_s=timeout_s)
            _httpx_final_url = str(_httpx_resp.url)
            _httpx_status = _httpx_resp.status
            _httpx_content_type = _httpx_resp.headers.get("Content-Type", "")
            _httpx_raw_ct = _httpx_content_type.split(";")[0].strip().lower()

            # Detect HTTP version from response
            _http_ver: str | None = None
            if hasattr(_httpx_resp, "extensions") and _httpx_resp.extensions:
                _http_ver = _httpx_resp.extensions.get("http_version", None)
                if _http_ver:
                    _http_ver = f"http/{_http_ver.decode() if isinstance(_http_ver, bytes) else _http_ver}"

            # Read body with size cap (mirrors aiohttp chunked read logic)
            _body_chunks: list[bytes] = []
            _total_read = 0
            async for _chunk in _httpx_resp.aiter_chunked(8192):
                _chunk_len = len(_chunk)
                if _total_read + _chunk_len > max_bytes:
                    _remaining = max_bytes - _total_read
                    if _remaining > 0:
                        _body_chunks.append(_chunk[:_remaining])
                        _total_read += _remaining
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    _tc.httpx_h2_count += 1
                    return FetchResult(
                        url=url,
                        final_url=_httpx_final_url,
                        status_code=_httpx_status,
                        content_type=_httpx_content_type,
                        text=None,
                        fetched_bytes=_total_read,
                        declared_length=-1,
                        elapsed_ms=elapsed_ms,
                        error="size_cap_exceeded",
                        failure_stage="size",
                        selected_transport="httpx_h2",
                        http_version=_http_ver,
                        transport_policy_reason=_httpx_reason,
                        transport_counters=_tc,
                    )
                _body_chunks.append(_chunk)
                _total_read += _chunk_len

            _body_bytes = b"".join(_body_chunks)
            _text, _decode_replaced, _decode_replacement_count = _try_decode(_body_bytes)

            elapsed_ms = (time.monotonic() - t0) * 1000
            redirected, redirect_target = _derive_redirect_fields(url, _httpx_final_url)
            _tc.httpx_h2_count += 1
            return FetchResult(
                url=url,
                final_url=_httpx_final_url,
                status_code=_httpx_status,
                content_type=_httpx_content_type,
                text=_text,
                fetched_bytes=_total_read,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=None,
                decode_replaced=_decode_replaced,
                decode_replacement_count=_decode_replacement_count,
                redirected=redirected,
                redirect_target=redirect_target,
                selected_transport="httpx_h2",
                http_version=_http_ver,
                transport_policy_reason=_httpx_reason,
                transport_counters=_tc,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as _e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            # F206AF: Record httpx_h2 failure and classify
            try:
                from hledac.universal.transport.httpx_transport import (
                    record_httpx_h2_failure,
                    classify_httpx_h2_error,
                )
                _httpx_err_type = classify_httpx_h2_error(_e)
                record_httpx_h2_failure()
            except asyncio.CancelledError:
                # F206AF: CancelledError MUST be re-raised, not caught
                raise
            except Exception:
                _httpx_err_type = "unknown_httpx_error"
            # HTTPX H2 failed — fallback to aiohttp with telemetry
            logger.warning(f"[HTTPX] H2 lane failed for {url} ({_httpx_err_type}), falling back to aiohttp: {_e}")
            _use_httpx_h2 = False
            _httpx_reason = "httpx_h2_fallback"
            # F206AF: Set transport_fallback_reason for this URL
            # (will be set on the FetchResult from the aiohttp fallback path)

    # Apply longer timeout for anonymized networks (Tor/I2P)
    if use_tor or use_i2p:
        timeout_s = timeout_s * TOR_STEALTH_TIMEOUT_SCALE

    # --- P16: Optional DoH resolution before connect ---
    _resolved_ip: Optional[str] = None
    if use_doh:
        try:
            from hledac.universal.security.passive_dns import resolve_doh
            parsed_url = urllib.parse.urlparse(url)
            hostname = parsed_url.hostname or ""
            if hostname:
                ips = await resolve_doh(hostname)
                if ips:
                    _resolved_ip = ips[0]
                    logger.debug(f"DoH resolved {hostname} → {_resolved_ip}")
                else:
                    logger.debug(f"DoH returned no IPs for {hostname}, falling back to system DNS")
        except Exception as e:
            logger.debug(f"DoH resolution failed for {url}: {e}")

    # --- P4: Canonical stealth session setup ---
    stealth_session = None
    if use_stealth:
        try:
            from hledac.universal.stealth.stealth_session import StealthSession
            stealth_session = StealthSession()
        except Exception as e:
            logger.warning(f"Stealth session unavailable, proceeding without: {e}")

    # --- F206M: curl_cffi stealth lane — explicit use_stealth escalation on clearnet ---
    # Respects should_use_curl_cffi guards: darknet/JS/Freenet are protected.
    # Falls back to aiohttp hot-path on any curl_cffi failure (including CancelledError re-raised).
    _curl_fallback_reason: str | None = None
    _use_curl, _curl_reason = should_use_curl_cffi(url, use_stealth=use_stealth, use_js=use_js)
    if _use_curl:
        try:
            _curl_result = await fetch_via_curl_cffi(
                url=url,
                headers=None,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
                profile="chrome110",
            )
            # Build FetchResult from curl_cffi dict — mirrors httpx_h2 success path
            _curl_text: str | None
            _curl_bytes = _curl_result.get("content", b"")
            _curl_decode_replaced = False
            _curl_decode_replacement_count = 0
            _curl_error = _curl_result.get("error", None)
            if _curl_bytes:
                _curl_text, _curl_decode_replaced, _curl_decode_replacement_count = _try_decode(_curl_bytes)
            else:
                _curl_text = None

            elapsed_ms = (time.monotonic() - t0) * 1000
            _curl_final_url = _curl_result.get("final_url", url)
            _curl_redirected, _curl_redirect_target = _derive_redirect_fields(url, _curl_final_url)
            _tc.curl_cffi_count += 1
            return FetchResult(
                url=url,
                final_url=_curl_final_url,
                status_code=_curl_result.get("status_code", 0),
                content_type=_curl_result.get("content_type", ""),
                text=_curl_text,
                fetched_bytes=len(_curl_bytes),
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=_curl_error,
                decode_replaced=_curl_decode_replaced,
                decode_replacement_count=_curl_decode_replacement_count,
                redirected=_curl_redirected,
                redirect_target=_curl_redirect_target,
                failure_stage=_curl_result.get("failure_stage", None),
                network_error_kind=_curl_result.get("network_error_kind", None),
                selected_transport="curl_cffi",
                http_version=None,  # curl_cffi doesn't expose HTTP version
                transport_policy_reason=_curl_reason,
                transport_fallback_reason=None,
                transport_counters=_tc,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as _curl_e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(f"[curl_cffi] stealth lane failed for {url}, falling back to aiohttp: {_curl_e}")
            _curl_fallback_reason = f"curl_cffi_failed:{type(_curl_e).__name__}"
            _tc.curl_cffi_fallback_to_aiohttp_count += 1
            _tc.fallback_count += 1

    # --- P4: Tor session setup for .onion URLs ---
    tor_session = None  # Always defined for use_tor check below
    if use_tor:
        try:
            tor_session = await _get_tor_session()
        except RuntimeError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.tor_aiohttp_socks_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=f"tor_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
                selected_transport="aiohttp_socks",
                transport_policy_reason="darknet_url",
                transport_counters=_tc,
            )

    # --- P10: I2P session setup for .i2p/.b32.i2p URLs ---
    i2p_session = None  # Always defined for use_i2p check below
    if use_i2p:
        try:
            i2p_session = await _get_i2p_session()
        except RuntimeError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.i2p_aiohttp_socks_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=f"i2p_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
                selected_transport="aiohttp_socks",
                transport_policy_reason="darknet_url",
                transport_counters=_tc,
            )

    # --- Retryable status tracking ---
    retry_after: float | None = None
    last_status_code: int = 0
    last_error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        # P4: Apply jitter before each request (Tor/stealth/I2P anti-correlation)
        if use_tor or use_i2p:
            await _jitter_delay()
        elif stealth_session is not None:
            # Canonical stealth: timing variance via StealthSession
            await stealth_session.apply_jitter()

        # P4: Maybe renew Tor circuit every N requests
        if use_tor:
            await _maybe_renew_tor_circuit()

        session = tor_session if use_tor else (i2p_session if use_i2p else await async_get_aiohttp_session())
        # F191B: Use separate semaphore pools — Tor/I2P cannot starve clearnet
        _semaphore = get_tor_semaphore() if (use_tor or use_i2p) else get_clearnet_semaphore()
        # Canonical stealth: use StealthSession UA rotation
        if stealth_session is not None:
            headers = {"User-Agent": stealth_session.rotate_ua()}
        else:
            headers = {"User-Agent": DEFAULT_UA}

        # P16: DoH resolution provides IP for logging/fallback but does NOT
        # override the Host header. The Host header must always be derived
        # from the URL's hostname to prevent host header injection.
        # P1-5: SSRF NOTE - aiohttp auto-follows redirects without validating
        # redirect targets. This is a known gap; httpx path has manual redirect
        # validation. The aiohttp path trusts the OS-level DNS which provides
        # some protection against DNS rebinding, but explicit redirect URL
        # validation would be safer. For now, auto-redirect is kept to avoid
        # breaking functionality; SSRF risk is partially mitigated by:
        #   1. OS DNS resolution returning public IPs for legitimate domains
        #   2. Tor/I2P sessions routing through proxies that block private IPs
        #   3. fetch_coordinator._validate_fetch_target() validating initial URL
        request_kwargs: dict = {"headers": headers, "allow_redirects": True}

        # F191B: Lightweight backpressure when RAM > M1_FETCH_SOFT_CEILING_GB — don't resize semaphore, just slow down
        # Sprint F206AL: 5.5GB ceiling now unified via uma_budget.M1_FETCH_SOFT_CEILING_GB
        if not use_tor and not use_i2p and not use_stealth:
            try:
                rss_gb = psutil.Process().memory_info().rss / 1e9
                if rss_gb > M1_FETCH_SOFT_CEILING_GB:
                    await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug(f"Memory check failed (non-fatal): {e}")

        try:
            async with asyncio.timeout(timeout_s):
                async with _semaphore:
                    async with session.get(url, **request_kwargs) as resp:
                        final_url = str(resp.url)
                        last_status_code = resp.status
                        content_type = resp.headers.get("Content-Type", "")
                        raw_content_type = content_type.split(";")[0].strip().lower()

                        # --- F206AJ: 403/429 one-shot curl_cffi escalation ---
                        # One-shot: aiohttp got 403/429 → try curl_cffi once before retry/body.
                        # Protected: darknet/JS/Freenet already handled upstream.
                        # No loop: escalation only on first attempt (attempt==0).
                        _escalated_to_curl = False
                        if last_status_code in (403, 429) and attempt == 0:
                            _env_curl = os.environ.get("HLEDAC_ENABLE_CURL_CFFI", "")
                            if _env_curl == "1":
                                _esc_use_curl, _esc_curl_reason = should_use_curl_cffi(
                                    url, use_stealth=use_stealth, use_js=use_js, prior_status=last_status_code
                                )
                                if _esc_use_curl:
                                    try:
                                        _esc_result = await fetch_via_curl_cffi(
                                            url=url,
                                            headers=None,
                                            timeout_s=timeout_s,
                                            max_bytes=max_bytes,
                                            profile="chrome110",
                                        )
                                        if _esc_result.get("status_code", 0) // 100 == 2:
                                            # curl succeeded with 2xx → return immediately
                                            _escalated_to_curl = True
                                            _tc.curl_cffi_count += 1
                                            _esc_bytes = _esc_result.get("content", b"")
                                            _esc_text: str | None
                                            _esc_decode_replaced = False
                                            _esc_decode_replacement_count = 0
                                            if _esc_bytes:
                                                _esc_text, _esc_decode_replaced, _esc_decode_replacement_count = _try_decode(_esc_bytes)
                                            else:
                                                _esc_text = None
                                            _esc_elapsed_ms = (time.monotonic() - t0) * 1000
                                            _esc_final_url = _esc_result.get("final_url", url)
                                            _esc_redirected, _esc_redirect_target = _derive_redirect_fields(url, _esc_final_url)
                                            return FetchResult(
                                                url=url,
                                                final_url=_esc_final_url,
                                                status_code=_esc_result.get("status_code", 0),
                                                content_type=_esc_result.get("content_type", ""),
                                                text=_esc_text,
                                                fetched_bytes=len(_esc_bytes),
                                                declared_length=-1,
                                                elapsed_ms=_esc_elapsed_ms,
                                                error=_esc_result.get("error", None),
                                                decode_replaced=_esc_decode_replaced,
                                                decode_replacement_count=_esc_decode_replacement_count,
                                                redirected=_esc_redirected,
                                                redirect_target=_esc_redirect_target,
                                                failure_stage=_esc_result.get("failure_stage", None),
                                                network_error_kind=_esc_result.get("network_error_kind", None),
                                                selected_transport="curl_cffi",
                                                http_version=None,
                                                transport_policy_reason=_esc_curl_reason,
                                                transport_fallback_reason="aiohttp_status_403_or_429_to_curl_cffi",
                                                transport_counters=_tc,
                                            )
                                        else:
                                            # curl returned non-2xx → fall through to aiohttp retry
                                            _curl_fallback_reason = f"curl_cffi_status_{_esc_result.get('status_code', 0)}_to_aiohttp"
                                            _tc.curl_cffi_fallback_to_aiohttp_count += 1
                                            _tc.fallback_count += 1
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as _esc_e:
                                        _curl_fallback_reason = f"curl_cffi_failed:{type(_esc_e).__name__}"
                                        _tc.curl_cffi_fallback_to_aiohttp_count += 1
                                        _tc.fallback_count += 1

                        # --- Retryable status → wait and retry once ---
                        if _circuit_breaker and _is_retryable_status(last_status_code):
                            _circuit_breaker.record_failure(failure_kind=str(last_status_code))
                            last_error = _build_retry_error(last_status_code, retry_after)
                            if attempt < MAX_RETRIES:
                                retry_after = _extract_retry_after(resp.headers)
                                backoff = _compute_backoff_seconds(retry_after, attempt)
                                await asyncio.sleep(backoff)
                                continue
                        # --- Content-type gate with XML-ish body recovery (Feed ingress hardening F164A) ---
                        xml_recovered = False
                        rejected_ct = raw_content_type not in ACCEPTED_CONTENT_TYPES

                        raw_declared = resp.headers.get("Content-Length")
                        try:
                            declared_length = int(raw_declared) if raw_declared else -1
                        except (ValueError, TypeError):
                            declared_length = -1

                        # --- Chunked body read with size cap ---
                        body_chunks: list[bytes] = []
                        total_read = 0
                        accumulated_ok = True
                        first_chunk_peeked = False

                        async for chunk in resp.content.iter_chunked(8192):
                            chunk_len = len(chunk)

                            # Peek: check first chunk for XML-ish body when CT is wrong
                            if rejected_ct and not first_chunk_peeked:
                                first_chunk_peeked = True
                                if _looks_xmlish(chunk):
                                    # Feed ingress recovery: wrong CT but XML body — accept it
                                    xml_recovered = True
                                elif total_read == 0:
                                    # First chunk is not XML-ish and we haven't accumulated anything —
                                    # non-XML body under wrong CT: reject without reading remainder
                                    elapsed_ms = (time.monotonic() - t0) * 1000
                                    redirected, redirect_target = _derive_redirect_fields(url, final_url)
                                    if _httpx_reason == "httpx_h2_fallback":
                                        _tc.httpx_h2_fallback_to_aiohttp_count += 1
                                        _tc.fallback_count += 1
                                    elif _curl_fallback_reason is not None:
                                        _tc.fallback_count += 1
                                    elif use_tor:
                                        _tc.tor_aiohttp_socks_count += 1
                                    elif use_i2p:
                                        _tc.i2p_aiohttp_socks_count += 1
                                    else:
                                        _tc.aiohttp_count += 1
                                    return FetchResult(
                                        url=url,
                                        final_url=final_url,
                                        status_code=last_status_code,
                                        content_type=content_type,
                                        text=None,
                                        fetched_bytes=0,
                                        declared_length=declared_length,
                                        elapsed_ms=elapsed_ms,
                                        error=f"content_type_rejected:{raw_content_type}",
                                        redirected=redirected,
                                        redirect_target=redirect_target,
                                        failure_stage="http",
                                        selected_transport="httpx_h2" if _use_httpx_h2 else ("aiohttp_socks" if (use_tor or use_i2p) else "aiohttp"),
                                        transport_policy_reason=_httpx_reason if _use_httpx_h2 else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),
                                        transport_fallback_reason="httpx_h2_fallback" if _httpx_reason == "httpx_h2_fallback" else None,
                                        transport_counters=_tc,
                                    )

                            if total_read + chunk_len > max_bytes:
                                remaining = max_bytes - total_read
                                if remaining > 0:
                                    body_chunks.append(chunk[:remaining])
                                    total_read += remaining
                                accumulated_ok = False
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                redirected, redirect_target = _derive_redirect_fields(url, final_url)
                                if _httpx_reason == "httpx_h2_fallback":
                                    _tc.httpx_h2_fallback_to_aiohttp_count += 1
                                    _tc.fallback_count += 1
                                elif _curl_fallback_reason is not None:
                                    _tc.fallback_count += 1
                                elif use_tor:
                                    _tc.tor_aiohttp_socks_count += 1
                                elif use_i2p:
                                    _tc.i2p_aiohttp_socks_count += 1
                                else:
                                    _tc.aiohttp_count += 1
                                return FetchResult(
                                    url=url,
                                    final_url=final_url,
                                    status_code=last_status_code,
                                    content_type=content_type,
                                    text=None,
                                    fetched_bytes=total_read,
                                    declared_length=declared_length,
                                    elapsed_ms=elapsed_ms,
                                    error="size_cap_exceeded",
                                    redirected=redirected,
                                    redirect_target=redirect_target,
                                    failure_stage="size",
                                    selected_transport="httpx_h2" if _use_httpx_h2 else ("aiohttp_socks" if (use_tor or use_i2p) else "aiohttp"),
                                    transport_policy_reason=_httpx_reason if _use_httpx_h2 else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),
                                    transport_fallback_reason="httpx_h2_fallback" if _httpx_reason == "httpx_h2_fallback" else None,
                                    transport_counters=_tc,
                                )
                            body_chunks.append(chunk)
                            total_read += chunk_len

                        if accumulated_ok and body_chunks:
                            try:
                                body_bytes = b"".join(body_chunks)
                                # F178E: detect decode quality — replacement count for truth
                                text, decode_replaced, decode_replacement_count = _try_decode(body_bytes)
                            except Exception as e:
                                logger.warning("Decode error in _try_decode: %s", e)
                                text = None
                                decode_replaced = False
                                decode_replacement_count = 0
                        else:
                            text = None
                            decode_replaced = False
                            decode_replacement_count = 0

                        # P7: Auto-detect JS need and retry via Camoufox → nodriver
                        # F207F: Skip JS retry for feed URLs, XML content-types, or when browser is unavailable
                        skip_js_reason: str | None = None
                        if text and not use_js and _needs_js_fetch(text):
                            if _browser_unavailable:
                                skip_js_reason = "browser_unavailable"
                            elif _looks_like_feed_url(url):
                                skip_js_reason = "xml_or_feed_url"
                            elif xml_recovered:
                                skip_js_reason = "xml_recovered"

                            if skip_js_reason:
                                logger.debug(
                                    f"JS renderer skipped for {url}: reason={skip_js_reason}"
                                )
                            else:
                                logger.info(f"JS need detected, retrying with Camoufox: {url}")
                                js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
                                if js_html:
                                    # Process JS-rendered HTML
                                    js_text, js_matches = await process_html_payload(js_html, url)
                                    elapsed_ms = (time.monotonic() - t0) * 1000
                                    _tc.js_renderer_count += 1
                                    return FetchResult(
                                        url=url,
                                        final_url=url,
                                        status_code=200,
                                        content_type="text/html",
                                        text=js_text,
                                        fetched_bytes=len(js_html),
                                        declared_length=-1,
                                        elapsed_ms=elapsed_ms,
                                        error=None,
                                        selected_transport="js",
                                        transport_policy_reason="js_required",
                                        transport_counters=_tc,
                                    )
                                # Camoufox failed → try nodriver fallback
                                logger.warning(f"Camoufox failed, trying nodriver: {url}")
                                js_html = await _fetch_with_nodriver(url)
                                if js_html:
                                    js_text, js_matches = await process_html_payload(js_html, url)
                                    elapsed_ms = (time.monotonic() - t0) * 1000
                                    _tc.js_renderer_count += 1
                                    return FetchResult(
                                        url=url,
                                        final_url=url,
                                        status_code=200,
                                        content_type="text/html",
                                        text=js_text,
                                        fetched_bytes=len(js_html),
                                        declared_length=-1,
                                        elapsed_ms=elapsed_ms,
                                        error=None,
                                        selected_transport="js",
                                        transport_policy_reason="js_required",
                                        transport_counters=_tc,
                                    )
                                # F207F: Both JS renders failed — mark browser unavailable if binary missing
                                if not js_html:
                                    _browser_unavailable = True
                                    logger.warning(f"All JS renders failed for {url}, returning aiohttp result")

                        elapsed_ms = (time.monotonic() - t0) * 1000
                        if _circuit_breaker and last_status_code >= 200 and last_status_code < 300:
                            _circuit_breaker.record_success()
                        redirected, redirect_target = _derive_redirect_fields(url, final_url)
                        # Determine actual transport used
                        _actual_transport = "httpx_h2" if _use_httpx_h2 else "aiohttp"
                        _fallback_info: str | None = None
                        # curl_cffi fallback takes priority — set when curl lane failed and aiohttp succeeded
                        if _curl_fallback_reason:
                            _fallback_info = _curl_fallback_reason
                        elif not _use_httpx_h2 and _httpx_reason == "httpx_h2_fallback":
                            _fallback_info = "httpx_h2_fallback"
                        # --- F206N: Transport counter for aiohttp success ---
                        if _curl_fallback_reason:
                            # curl fallback counter already incremented in curl except block
                            pass
                        elif _fallback_info == "httpx_h2_fallback":
                            _tc.httpx_h2_fallback_to_aiohttp_count += 1
                            _tc.fallback_count += 1
                        elif use_tor:
                            _tc.tor_aiohttp_socks_count += 1
                        elif use_i2p:
                            _tc.i2p_aiohttp_socks_count += 1
                        else:
                            _tc.aiohttp_count += 1
                        return FetchResult(
                            url=url,
                            final_url=final_url,
                            status_code=last_status_code,
                            content_type=content_type,
                            text=text,
                            fetched_bytes=total_read,
                            declared_length=declared_length,
                            elapsed_ms=elapsed_ms,
                            error=None,
                            xml_recovered=xml_recovered,
                            xml_source_hint=xml_recovered,  # F178E: xml_source_hint mirrors xml_recovered
                            decode_replaced=decode_replaced,
                            decode_replacement_count=decode_replacement_count,  # F178E
                            redirected=redirected,
                            redirect_target=redirect_target,
                            selected_transport=_actual_transport,
                            http_version="http/1.1",  # aiohttp always HTTP/1.1
                            transport_policy_reason=_httpx_reason if _use_httpx_h2 else "clearnet_default",
                            transport_fallback_reason=_fallback_info,
                            transport_counters=_tc,
                            js_renderer_skipped_reason=skip_js_reason,  # F207F
                        )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if _circuit_breaker:
                _circuit_breaker.record_failure(is_timeout=True, failure_kind="timeout")
            # --- F206N: Transport counter for timeout ---
            if _curl_fallback_reason:
                pass  # curl fallback counter already incremented
            elif _httpx_reason == "httpx_h2_fallback":
                _tc.httpx_h2_fallback_to_aiohttp_count += 1
                _tc.fallback_count += 1
            elif use_tor:
                _tc.tor_aiohttp_socks_count += 1
            elif use_i2p:
                _tc.i2p_aiohttp_socks_count += 1
            else:
                _tc.aiohttp_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error="timeout",
                failure_stage="connection",
                network_error_kind="timeout",
                selected_transport="httpx_h2" if _use_httpx_h2 else ("aiohttp_socks" if (use_tor or use_i2p) else "aiohttp"),
                transport_policy_reason=_httpx_reason if _use_httpx_h2 else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),
                transport_fallback_reason="httpx_h2_fallback" if _httpx_reason == "httpx_h2_fallback" else None,
                transport_counters=_tc,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if _circuit_breaker:
                _circuit_breaker.record_failure(failure_kind="fetch_error")
            err_str = f"fetch_error;{type(exc).__name__};{exc}"
            failure_stage, network_error_kind = _derive_failure_stage_and_network_kind(err_str)
            # body_read_error=True only when body stream was actually entered and failed.
            # For connection/tls/http stages the body was never reached — flag stays False.
            body_read_error = failure_stage in ("body", "size")
            # --- F206N: Transport counter for exception ---
            if _curl_fallback_reason:
                pass  # curl fallback counter already incremented
            elif _httpx_reason == "httpx_h2_fallback":
                _tc.httpx_h2_fallback_to_aiohttp_count += 1
                _tc.fallback_count += 1
            elif use_tor:
                _tc.tor_aiohttp_socks_count += 1
            elif use_i2p:
                _tc.i2p_aiohttp_socks_count += 1
            else:
                _tc.aiohttp_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=err_str,
                body_read_error=body_read_error,
                failure_stage=failure_stage,
                network_error_kind=network_error_kind,
                selected_transport="httpx_h2" if _use_httpx_h2 else ("aiohttp_socks" if (use_tor or use_i2p) else "aiohttp"),
                transport_policy_reason=_httpx_reason if _use_httpx_h2 else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),
                transport_fallback_reason="httpx_h2_fallback" if _httpx_reason == "httpx_h2_fallback" else None,
                transport_counters=_tc,
            )

    # Should not reach here, but as safeguard (retry exhaustion after loop):
    elapsed_ms = (time.monotonic() - t0) * 1000
    err_str = last_error or "retry_exhausted"
    failure_stage, network_error_kind = _derive_failure_stage_and_network_kind(err_str)
    body_read_error = failure_stage in ("body", "size")
    # --- F206N: Transport counter (same logic as retry exhausted) ---
    if _curl_fallback_reason:
        pass  # curl fallback counter already incremented
    elif _httpx_reason == "httpx_h2_fallback":
        _tc.httpx_h2_fallback_to_aiohttp_count += 1
        _tc.fallback_count += 1
    elif use_tor:
        _tc.tor_aiohttp_socks_count += 1
    elif use_i2p:
        _tc.i2p_aiohttp_socks_count += 1
    else:
        _tc.aiohttp_count += 1
    return FetchResult(
        url=url,
        final_url=url,
        status_code=last_status_code,
        content_type="",
        text=None,
        fetched_bytes=0,
        declared_length=-1,
        elapsed_ms=elapsed_ms,
        error=err_str,
        body_read_error=body_read_error,
        failure_stage=failure_stage,
        network_error_kind=network_error_kind,
        selected_transport="httpx_h2" if _use_httpx_h2 else ("aiohttp_socks" if (use_tor or use_i2p) else "aiohttp"),
        transport_policy_reason=_httpx_reason if _use_httpx_h2 else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),
        transport_fallback_reason="httpx_h2_fallback" if _httpx_reason == "httpx_h2_fallback" else None,
        transport_counters=_tc,
    )


__all__ = [
    "async_fetch_public_text",
    "process_html_payload",
    "DEFAULT_UA",
    "MAX_BYTES_DEFAULT",
    "MAX_BYTES_HARD",
    "MAX_RETRIES",
    "FetchResult",
    "_is_retryable_status",
    "_extract_retry_after",
    "_compute_backoff_seconds",
    "_try_decode",
    "_looks_xmlish",
    # P4: Tor + stealth helpers
    "_is_onion_url",
    "_get_tor_session",
    "_renew_tor_circuit",
    "_jitter_delay",
    "_close_tor_session",
    "TOR_SOCKS_PROXY",
    "TOR_CIRCUIT_RENEWAL_REQUEST_COUNT",
    # P10: I2P + Freenet helpers
    "_is_i2p_url",
    "_is_freenet_url",
    "_get_i2p_session",
    "_close_i2p_session",
    # P7: JS rendering helpers
    "_needs_js_fetch",
    "_fetch_with_camoufox",
    "_fetch_with_nodriver",
    # F206AT: Pool authority seam
    "PUBLIC_FETCHER_POOL_AUTHORITY",
    "inject_session_provider",
    "get_session_source_telemetry",
]

# ---------------------------------------------------------------------------
# HTML → text + pattern matching (CPU-bound, runs in shared CPU_EXECUTOR)
# ---------------------------------------------------------------------------
from hledac.universal.utils.executors import CPU_EXECUTOR


def _sync_process_html(html: str) -> tuple[str, list]:
    """Synchronous CPU-bound HTML parsing + pattern matching.

    Runs in CPU_EXECUTOR thread pool — never blocks the async event loop.
    Fail-safe: malformed HTML returns empty text, never raises.
    """
    # Note: PatternMatcher is bootstrapped once at startup via
    # configure_default_bootstrap_patterns_if_empty() in pattern_matcher.py.
    # Re-configuring on every call wastes CPU — removed per F184B.

    # markdownify with plaintext fallback
    try:
        import markdownify as _md

        text = _md.markdownify(html, strip=["script", "style"], heading_style="ATX")
    except Exception as e:
        logger.warning("markdownify failed, using regex fallback: %s", e)
        import html as _html

        text = re.sub(r"<[^>]+>", " ", _html.unescape(html))
        text = re.sub(r"\s{2,}", " ", text).strip()

    # Pattern scan
    matches = match_text(text)
    return (text, matches)


async def process_html_payload(html: str, url: str) -> tuple[str, list]:
    """Offload HTML→text+pattern matching to shared CPU_EXECUTOR.

    Args:
        html: Raw HTML content.
        url: Source URL (for context in errors; not used for fetching).

    Returns:
        Tuple of (markdown-stripped text, pattern match list).
        Never raises — malformed HTML returns (stripped_text, []) on fallback.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(CPU_EXECUTOR, _sync_process_html, html)
