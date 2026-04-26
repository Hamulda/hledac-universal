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

import asyncio
import logging
import random
import re
import time
import urllib.parse
from typing import Final, Optional

import psutil

import httpx

import msgspec

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.patterns.pattern_matcher import match_text
from hledac.universal.transport.circuit_breaker import (
    get_breaker,
    CircuitBreaker,
    CircuitDecision,
)
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

# P7: Camoufox singleton lock — max 1 instance across entire fetcher
_CAMOUFOX_LOCK: "asyncio.Lock" = asyncio.Lock()

# ---------------------------------------------------------------------------
# F191B: httpx timeout hierarchy — separate clearnet vs Tor paths
# ---------------------------------------------------------------------------
# httpx gives us HTTP/2 multiplexing and better connection pooling than aiohttp
# httpx is used ONLY for clearnet (non-Tor, non-I2P) fetches.
# Tor/I2P retain aiohttp + aiohttp_socks (cannot change — task constraint).

_CLEARNET_HTTX_CLIENT: Optional[httpx.AsyncClient] = None
_CLEARNET_HTTX_LOCK: "asyncio.Lock" = asyncio.Lock()

# Timeout hierarchy: httpx.Timeout(connect, read, write, pool)
# clearnet: fast — connect 3s, read 8s, write 3s, pool 2s
CLEARNET_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=3.0,
    read=8.0,
    write=3.0,
    pool=2.0,
)
# Tor: slow by design — circuit setup takes time
TOR_HTTX_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=10.0,
    read=20.0,
    write=5.0,
    pool=5.0,
)


def _http2_available() -> bool:
    """Check if h2 (HTTP/2) package is available."""
    try:
        import h2
        return True
    except ImportError:
        return False


async def _get_clearnet_httpx_client() -> httpx.AsyncClient:
    """
    Get or create the shared httpx.AsyncClient for clearnet fetches (lazy singleton).

    Connection pool: max_connections=50, max_keepalive_connections=20.
    HTTP/2 multiplexing enabled if h2 package available (major perf win).
    Falls back to HTTP/1.1 gracefully if h2 not installed.
    """
    global _CLEARNET_HTTX_CLIENT
    async with _CLEARNET_HTTX_LOCK:
        if _CLEARNET_HTTX_CLIENT is None or _CLEARNET_HTTX_CLIENT.is_closed:
            use_http2 = _http2_available()
            _CLEARNET_HTTX_CLIENT = httpx.AsyncClient(
                timeout=CLEARNET_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
                http2=use_http2,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_UA},
            )
            logger.debug(f"[CLEARNET_HTTX] httpx.AsyncClient created (HTTP/2={use_http2}, pool=50)")
        return _CLEARNET_HTTX_CLIENT


async def _close_clearnet_httpx_client() -> None:
    """Close the shared httpx clearnet client (idempotent)."""
    global _CLEARNET_HTTX_CLIENT
    async with _CLEARNET_HTTX_LOCK:
        if _CLEARNET_HTTX_CLIENT is not None and not _CLEARNET_HTTX_CLIENT.is_closed:
            await _CLEARNET_HTTX_CLIENT.aclose()
            _CLEARNET_HTTX_CLIENT = None
            logger.debug("[CLEARNET_HTTX] httpx.AsyncClient closed")

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
    except Exception:
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
    except Exception:
        return False


def _is_i2p_url(url: str) -> bool:
    """
    P10: Detect if URL targets an I2P address (.i2p or .b32.i2p).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        return hostname.endswith(".i2p") or hostname.endswith(".b32.i2p")
    except Exception:
        return False


def _is_freenet_url(url: str) -> bool:
    """
    P10: Detect if URL targets a Freenet address (.freenet).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname.lower().endswith(".freenet") if parsed.hostname else False
    except Exception:
        return False


async def _get_tor_session() -> "aiohttp.ClientSession":
    """Get or create aiohttp session via Tor SOCKS5 proxy (lazy, singleton)."""
    global _tor_session
    if _tor_session is None or _tor_session.closed:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise RuntimeError("aiohttp_socks required for Tor: pip install aiohttp_socks")
        connector = ProxyConnector.from_url(TOR_SOCKS_PROXY, rdns=True)
        _tor_session = aiohttp.ClientSession(connector=connector)
    return _tor_session


async def _get_i2p_session() -> "aiohttp.ClientSession":
    """
    P10: Get or create aiohttp session via I2P SOCKS5 proxy (lazy, singleton).
    Uses aiohttp_socks.ProxyConnector for .i2p/.b32.i2p URLs.
    """
    global _i2p_session
    if _i2p_session is None or _i2p_session.closed:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise RuntimeError("aiohttp_socks required for I2P: pip install aiohttp_socks")
        # I2P default SOCKS port is 7654
        connector = ProxyConnector.from_url("socks5://127.0.0.1:7654", rdns=True)
        _i2p_session = aiohttp.ClientSession(connector=connector)
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


async def _close_i2p_session() -> None:
    """
    P10: Close the I2P session (for cleanup).
    """
    global _i2p_session
    if _i2p_session is not None and not _i2p_session.closed:
        await _i2p_session.close()
        _i2p_session = None


# ---------------------------------------------------------------------------
# P7: JS detection and Camoufox/nodriver rendering
# ---------------------------------------------------------------------------

# JS detection patterns — trigger Camoufox retry
_NOSCRIPT_RE = re.compile(r"<noscript[^>]*>|enable javascript", re.IGNORECASE)


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
    except Exception:
        pass  # Fail-open: if import fails, proceed with caution

    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        logger.debug("camoufox not installed, JS fetch unavailable")
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
            )
        # JS rendering completely failed
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
            error="js_render_failed",
            failure_stage="fetching",
        )

    # --- P4: Determine transport mode ---
    is_onion = _is_onion_url(url)
    is_i2p = _is_i2p_url(url)
    is_freenet = _is_freenet_url(url)
    use_tor = is_onion  # .onion URLs always go via Tor
    use_i2p = is_i2p  # .i2p/.b32.i2p URLs go via I2P SOCKS
    use_freenet = is_freenet  # .freenet URLs go via Freenet HTTP proxy

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

    # --- P4: Tor session setup for .onion URLs ---
    tor_session = None  # Always defined for use_tor check below
    if use_tor:
        try:
            tor_session = await _get_tor_session()
        except RuntimeError as e:
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
                error=f"tor_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
            )

    # --- P10: I2P session setup for .i2p/.b32.i2p URLs ---
    i2p_session = None  # Always defined for use_i2p check below
    if use_i2p:
        try:
            i2p_session = await _get_i2p_session()
        except RuntimeError as e:
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
                error=f"i2p_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
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
        request_kwargs: dict = {"headers": headers, "allow_redirects": True}

        # F191B: Lightweight backpressure when RAM > 5.5 GB — don't resize semaphore, just slow down
        if not use_tor and not use_i2p and not use_stealth:
            try:
                rss_gb = psutil.Process().memory_info().rss / 1e9
                if rss_gb > 5.5:
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

                        # --- Retryable status → wait and retry once ---
                        if _circuit_breaker and _is_retryable_status(last_status_code):
                            _circuit_breaker.record_failure(failure_kind=str(last_status_code))
                        last_error = _build_retry_error(last_status_code, retry_after)
                        if attempt < MAX_RETRIES:
                            retry_after = _extract_retry_after(resp.headers)
                            backoff = _compute_backoff_seconds(retry_after, attempt)
                            await asyncio.sleep(backoff)
                            continue
                        # Exhausted retries — return with error prefix
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        redirected, redirect_target = _derive_redirect_fields(url, final_url)
                        return FetchResult(
                            url=url,
                            final_url=final_url,
                            status_code=last_status_code,
                            content_type=content_type,
                            text=None,
                            fetched_bytes=0,
                            declared_length=-1,
                            elapsed_ms=elapsed_ms,
                            error=last_error,
                            redirected=redirected,
                            redirect_target=redirect_target,
                            failure_stage="http",
                        )

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
                                    )

                            if total_read + chunk_len > max_bytes:
                                remaining = max_bytes - total_read
                                if remaining > 0:
                                    body_chunks.append(chunk[:remaining])
                                    total_read += remaining
                                accumulated_ok = False
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                redirected, redirect_target = _derive_redirect_fields(url, final_url)
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
                                )
                            body_chunks.append(chunk)
                            total_read += chunk_len

                        if accumulated_ok and body_chunks:
                            try:
                                body_bytes = b"".join(body_chunks)
                                # F178E: detect decode quality — replacement count for truth
                                text, decode_replaced, decode_replacement_count = _try_decode(body_bytes)
                            except Exception:
                                text = None
                                decode_replaced = False
                                decode_replacement_count = 0
                        else:
                            text = None
                            decode_replaced = False
                            decode_replacement_count = 0

                        # P7: Auto-detect JS need and retry via Camoufox → nodriver
                        if text and not use_js and _needs_js_fetch(text):
                            logger.info(f"JS need detected, retrying with Camoufox: {url}")
                            js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
                            if js_html:
                                # Process JS-rendered HTML
                                js_text, js_matches = await process_html_payload(js_html, url)
                                elapsed_ms = (time.monotonic() - t0) * 1000
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
                                )
                            # Camoufox failed → try nodriver fallback
                            logger.warning(f"Camoufox failed, trying nodriver: {url}")
                            js_html = await _fetch_with_nodriver(url)
                            if js_html:
                                js_text, js_matches = await process_html_payload(js_html, url)
                                elapsed_ms = (time.monotonic() - t0) * 1000
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
                                )
                            # Both JS renders failed → warn and return original
                            logger.warning(f"All JS renders failed for {url}, returning aiohttp result")

                        elapsed_ms = (time.monotonic() - t0) * 1000
                        if _circuit_breaker and last_status_code >= 200 and last_status_code < 300:
                            _circuit_breaker.record_success()
                        redirected, redirect_target = _derive_redirect_fields(url, final_url)
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
                        )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if _circuit_breaker:
                _circuit_breaker.record_failure(is_timeout=True, failure_kind="timeout")
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
            )

    # Should not reach here, but as safeguard (retry exhaustion after loop):
    elapsed_ms = (time.monotonic() - t0) * 1000
    err_str = last_error or "retry_exhausted"
    failure_stage, network_error_kind = _derive_failure_stage_and_network_kind(err_str)
    body_read_error = failure_stage in ("body", "size")
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
    except Exception:
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
