"""
URL Engine Wiring - ISSUE-007
=============================

Wires the zombie url_engine.rs Rust module to its proper Python integration points.

Rust Module: rust_extensions/src/url_engine.rs
Feature: core
Purpose: URL normalization and fingerprinting for OSINT deduplication

Integration Points:
-------------------
1. recon/ - URL normalization for deduplication
2. ioc/ - URL IOC canonicalization
3. knowledge/ - URL fingerprinting for dedup

API (from Rust):
----------------
- normalize(url: str) -> str
- fingerprint(url: str) -> u64
- strip_tracking_params(url: str) -> str
- TRACKING_PARAMS: list[str] - common tracking parameters to strip

Usage:
------
from rust_extensions.wiring import normalize_url, fingerprint_url, strip_tracking_params

# Normalize URL
canonical = normalize_url("https://Example.COM/path?b=2&a=1#fragment")
# -> "https://example.com/path?a=2&b=1"

# Fast fingerprint for dedup
fp = fingerprint_url("https://example.com/page")
# -> 64-bit integer

# Strip tracking params
clean = strip_tracking_params("https://example.com/?utm_source=foo&fbclid=bar")
# -> "https://example.com/"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from hledac.universal._core.rust_backend import rust as _rust_backend

# Check availability
_url_engine_available = (
    _rust_backend.is_available
    and hasattr(_rust_backend, "url_engine")
    and getattr(_rust_backend, "url_engine", None) is not None
)

# Get module reference
_url_engine = getattr(_rust_backend, "url_engine", None) if _url_engine_available else None


# =============================================================================
# URL Normalization Functions
# =============================================================================


def normalize_url(url: str) -> str:
    """
    Normalize URL to canonical form.

    Performs:
    1. Lowercase scheme and host
    2. Remove default ports (80 for http, 443 for https)
    3. Sort query parameters alphabetically
    4. Remove fragment

    Args:
        url: Raw URL string

    Returns:
        Canonical URL string

    Example:
        >>> normalize_url("https://Example.COM/path?b=2&a=1#fragment")
        'https://example.com/path?a=2&b=1'
    """
    if _url_engine is not None:
        try:
            return _url_engine.normalize(url)
        except Exception:
            pass

    # Python fallback
    return _python_normalize_url(url)


def _python_normalize_url(url: str) -> str:
    """Pure Python URL normalization fallback."""
    from urllib.parse import urlparse, urlencode, parse_qs

    parsed = urlparse(url)

    # Lowercase scheme and netloc
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower().split("@")[-1]  # Remove auth

    # Remove default port
    if ":" in netloc:
        host_port = netloc.rsplit(":", 1)
        if len(host_port) == 2:
            host, port = host_port
            if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
                netloc = host
            else:
                netloc = f"{host}:{port}"

    # Sort query params
    query = parsed.query
    if query:
        params = parse_qs(query)
        sorted_query = urlencode(sorted(params.items()))

    # Reconstruct URL without fragment
    return f"{scheme}://{netloc}{parsed.path}?{sorted_query if query else ''}"


def fingerprint_url(url: str) -> int:
    """
    Compute fast 64-bit fingerprint for URL deduplication.

    Combines canonicalization + xxhash3-64 hashing in one call.
    Much faster than hashing in Python.

    Args:
        url: URL string

    Returns:
        64-bit unsigned integer fingerprint

    Example:
        >>> fp = fingerprint_url("https://example.com/page")
        >>> type(fp)
        <class 'int'>
    """
    if _url_engine is not None:
        try:
            return _url_engine.fingerprint(url)
        except Exception:
            pass

    # Python fallback
    return _python_fingerprint_url(url)


def _python_fingerprint_url(url: str) -> int:
    """Pure Python URL fingerprint fallback using xxhash."""
    try:
        import xxhash

        normalized = _python_normalize_url(url)
        return xxhash.xxh3_64(normalized.encode())
    except ImportError:
        import hashlib

        normalized = _python_normalize_url(url)
        return int(hashlib.sha256(normalized.encode()).hexdigest()[:16], 16)


def strip_tracking_params(url: str) -> str:
    """
    Strip tracking parameters from URL.

    Removes common analytics/campaign parameters:
    - UTM parameters (utm_source, utm_medium, etc.)
    - Facebook: fbclid
    - Google: gclid, gclsrc
    - Microsoft: msclkid
    - Twitter: twclid
    - And many more...

    Args:
        url: URL with tracking parameters

    Returns:
        URL with tracking parameters removed

    Example:
        >>> strip_tracking_params("https://example.com/?utm_source=foo&fbclid=bar&page=1")
        'https://example.com/?page=1'
    """
    if _url_engine is not None:
        try:
            return _url_engine.strip_tracking_params(url)
        except Exception:
            pass

    # Python fallback
    return _python_strip_tracking_params(url)


# Common tracking parameters (matching Rust TRACKING_PARAMS)
_TRACKING_PARAMS: set[str] = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "msclkid",
    "twclid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "_gl",
    "yclid",
    "ymclid",
    "spm",
    "scm_source",
    "scm_content",
    "share_source",
    "share_medium",
    "ref",
    "referrer",
    "ref_src",
    "ref_url",
    "campaign",
    "source",
    "affiliate",
    "zanpid",
    "aff_id",
}


def _python_strip_tracking_params(url: str) -> str:
    """Pure Python tracking parameter stripping fallback."""
    from urllib.parse import urlparse, urlencode, parse_qs

    parsed = urlparse(url)
    if parsed.query:
        params = parse_qs(parsed.query)
        filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        query = urlencode(filtered, doseq=True) if filtered else ""
    else:
        query = ""

    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}"


def get_tracking_params() -> list[str]:
    """
    Get list of tracking parameters stripped by strip_tracking_params().

    Returns:
        List of tracking parameter names
    """
    if _url_engine is not None and hasattr(_url_engine, "TRACKING_PARAMS"):
        return list(_url_engine.TRACKING_PARAMS)

    return list(_TRACKING_PARAMS)


def url_engine_available() -> bool:
    """Check if Rust URL engine is available."""
    return _url_engine_available


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "normalize_url",
    "fingerprint_url",
    "strip_tracking_params",
    "get_tracking_params",
    "url_engine_available",
]
