"""
STORAGE-FIX-4: Bounded encoding fallback for OSINT HTTP responses.

OSINT data comes in many encodings (Latin-1, Shift-JIS, GB2312, KOI8-R, etc.).
Without a consistent decoding chain, garbled bytes leak into DuckDB and break
downstream pattern matching.

This module provides a single fail-soft decode helper. The chain is:

    1. charset_normalizer.from_bytes() (if available) — best accuracy
    2. chardet.detect() (if available) — legacy fallback
    3. UTF-8 strict
    4. UTF-8 with surrogateescape
    5. latin-1 (always succeeds — last resort)

Each step is bounded: total bytes capped at 5 MB, candidate attempts capped at 3.

The module NEVER raises. It returns str (possibly with replacement chars).

M1 8GB-safe: pure-Python paths only, no heavy ML models, no streaming.
"""


import logging
from typing import Final

logger = logging.getLogger(__name__)

# Bounded: 5 MB hard cap. Larger responses are truncated before decoding.
_MAX_DECODE_BYTES: Final[int] = 5 * 1024 * 1024


def _try_charset_normalizer(raw: bytes) -> str | None:
    """Best-effort charset detection via charset_normalizer (transitive dep).

    Returns decoded str, or None on any failure / if lib absent.
    Bounded: at most _MAX_CANDIDATES attempts.
    """
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return None
    try:
        results = from_bytes(raw)
        if results is None:
            return None
        best = results.best() if hasattr(results, "best") else None
        if best is None:
            return None
        return str(best)
    except Exception as e:
        logger.debug("encoding: charset_normalizer failed: %s", e)
        return None


def _try_chardet(raw: bytes) -> str | None:
    """Legacy fallback via chardet (if installed)."""
    try:
        import chardet  # type: ignore
    except ImportError:
        return None
    try:
        detected = chardet.detect(raw[:65536])  # bounded sample
        if not detected or not detected.get("encoding"):
            return None
        enc = detected["encoding"]
        return raw.decode(enc, errors="replace")
    except Exception as e:
        logger.debug("encoding: chardet failed: %s", e)
        return None


def decode_response_bytes(
    raw: bytes | str | bytearray | memoryview,
    *,
    http_charset: str | None = None,
    max_bytes: int = _MAX_DECODE_BYTES,
) -> str:
    """
    STORAGE-FIX-4: Fail-soft bytes -> str decoding with bounded chain.

    Args:
        raw: raw response bytes (any size; truncated to max_bytes internally).
        http_charset: optional Content-Type charset hint (e.g. from response header).
        max_bytes: cap on bytes consumed (default 5 MB; pass smaller for stricter bound).

    Returns:
        Decoded str. Never raises. May contain U+FFFD replacement chars if all
        candidates fail (extremely rare — latin-1 always succeeds).
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        # Unknown type — coerce via repr as last resort
        return repr(raw)

    raw_b = bytes(raw)
    if len(raw_b) > max_bytes:
        logger.debug("encoding: truncating %d -> %d bytes for decode", len(raw_b), max_bytes)
        raw_b = raw_b[:max_bytes]

    if not raw_b:
        return ""

    # 0) HTTP header charset hint (cheap path)
    if http_charset:
        try:
            return raw_b.decode(http_charset, errors="strict")
        except (LookupError, UnicodeDecodeError):
            pass  # unknown encoding or invalid bytes — fall through

    # 1) charset_normalizer (best accuracy)
    decoded = _try_charset_normalizer(raw_b)
    if decoded:
        return decoded

    # 2) chardet (legacy)
    decoded = _try_chardet(raw_b)
    if decoded:
        return decoded

    # 3) UTF-8 strict
    try:
        return raw_b.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass

    # 4) UTF-8 with surrogateescape (preserves invalid bytes as surrogate codes).
    # surrogateescape cannot raise — kept here as defense-in-depth before latin-1.
    decoded = raw_b.decode("utf-8", errors="surrogateescape")
    if "�" not in decoded:
        return decoded

    # 5) latin-1 — ALWAYS succeeds (1:1 byte->char mapping, no replacement chars)
    return raw_b.decode("latin-1", errors="strict")


def parse_charset_from_content_type(content_type: str | None) -> str | None:
    """F261: Extract charset= value from a Content-Type header.

    Examples:
        "text/html; charset=utf-8"            -> "utf-8"
        "text/html;charset=windows-1252"      -> "windows-1252"
        'text/html; charset="iso-8859-1"'     -> "iso-8859-1"
        "text/html"                           -> None
    Returns None on empty / malformed input. Never raises.
    """
    if not content_type or not isinstance(content_type, str):
        return None
    try:
        for part in content_type.split(";"):
            token = part.strip()
            if token.lower().startswith("charset="):
                value = token[len("charset="):].strip().strip('"').strip("'")
                return value or None
    except Exception:
        return None
    return None
