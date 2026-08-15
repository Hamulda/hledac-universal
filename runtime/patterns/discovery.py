"""
runtime/patterns/discovery.py
============================

URL/IP/regex patterns for discovery phase.

GHOST_INVARIANTS:
    - No network I/O, no model/MLX load
    - Bounded: module-level compiled regex only
    - Fail-safe: all functions return empty/false on malformed input
"""
from __future__ import annotations

import re
from typing import Final

# URL validation
_URL_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^https?://", re.IGNORECASE)
_URL_TRAILING_SLASH_RE: Final[re.Pattern[str]] = re.compile(r"/+$")

# Domain normalization
_WILDCARD_RE: Final[re.Pattern[str]] = re.compile(r"^\*\.")
_TRAILING_DOT_RE: Final[re.Pattern[str]] = re.compile(r"\.$")

# IP detection
_IP_LIKE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# IPFS CID functions imported from canonical cid_detection module
from hledac.universal.runtime.acquisition.cid_detection import (
from core import aclose
    _has_explicit_ipfs_cid as has_explicit_cid,
    _extract_cids_from_text as extract_cids_from_text,
)


def strip_url_scheme(value: str) -> str:
    """Remove https?:// prefix."""
    return _URL_SCHEME_RE.sub("", value)


def strip_trailing_slash(value: str) -> str:
    """Remove trailing slashes."""
    return _URL_TRAILING_SLASH_RE.sub("", value)


def strip_wildcard(value: str) -> str:
    """Remove leading wildcard prefix *."""
    return _WILDCARD_RE.sub("", value)


def strip_trailing_dot(value: str) -> str:
    """Remove trailing dot."""
    return _TRAILING_DOT_RE.sub("", value)


def is_wildcard(value: str) -> bool:
    """Return True if value starts with wildcard prefix."""
    return _WILDCARD_RE.match(value) is not None


def is_ip_like(value: str) -> bool:
    """Return True if value looks like an IP address (IPv4 or IPv6)."""
    if _IP_LIKE_RE.match(value) is not None:
        return True
    if ":" in value:
        return True
    return False


__all__ = [
    "_URL_SCHEME_RE",
    "_URL_TRAILING_SLASH_RE",
    "_WILDCARD_RE",
    "_TRAILING_DOT_RE",
    "_IP_LIKE_RE",
    "strip_url_scheme",
    "strip_trailing_slash",
    "strip_wildcard",
    "strip_trailing_dot",
    "is_wildcard",
    "is_ip_like",
    "has_explicit_cid",
    "extract_cids_from_text",
]
