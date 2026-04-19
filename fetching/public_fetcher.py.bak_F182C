# hledac/universal/fetching/public_fetcher.py
# Sprint 8AD — First live public text fetch adapter v1
# aiohttp/shared-session, chunked size-safe, timeout-safe, passive-only
"""
Public-passive text/HTML fetcher using shared aiohttp session runtime.
Always-on, bounded, fail-soft, typed via msgspec.Struct.
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from typing import Final

import msgspec

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns

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
# Main fetch function
# ---------------------------------------------------------------------------


async def async_fetch_public_text(
    url: str,
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
) -> FetchResult:
    """
    Fetch a public URL using the shared aiohttp session.

    Passive-only: no auth, no cookies, no stealth.
    Chunked streaming with hard size cap.
    CancelledError propagates (not swallowed).

    Parameters
    ----------
    url : str
        Target URL (http or https only).
    timeout_s : float
        Per-request timeout in seconds (default 35 s).
    max_bytes : int
        Maximum bytes to read from body (default 2 MB, hard cap 10 MB).

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

    # --- Size cap enforcement ---
    if max_bytes > MAX_BYTES_HARD:
        max_bytes = MAX_BYTES_HARD

    # --- Retryable status tracking ---
    retry_after: float | None = None
    last_status_code: int = 0
    last_error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        session = await async_get_aiohttp_session()
        headers = {"User-Agent": DEFAULT_UA}

        try:
            async with asyncio.timeout(timeout_s):
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    last_status_code = resp.status
                    content_type = resp.headers.get("Content-Type", "")
                    raw_content_type = content_type.split(";")[0].strip().lower()

                    # --- Retryable status → wait and retry once ---
                    if _is_retryable_status(last_status_code):
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

                    elapsed_ms = (time.monotonic() - t0) * 1000
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
    # Bootstrap patterns on first use (idempotent, thread-safe)
    configure_patterns(get_default_bootstrap_patterns())

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
