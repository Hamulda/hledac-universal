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

# IPFS CIDv0: Qm + 44 base58 chars = 46 chars total
_CIDV0_RE: Final[re.Pattern[str]] = re.compile(r"^Qm[1-9A-HJ-NP-Za-km-z]{44}$")

# IPFS CIDv1 base32: bafy + 50-59 base32 chars
_CIDV1_BASE32_RE: Final[re.Pattern[str]] = re.compile(r"^bafy[a-z2-7]{50,59}$")


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


def has_explicit_cid(value: str) -> bool:
    """
    Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).

    GHOST_INVARIANTS:
        - No network I/O, no model/MLX load
        - Bounded: O(1) length check before regex
        - Fail-safe: returns False for malformed input
    """
    if not value or len(value) < 46 or len(value) > 70:
        return False
    if value.startswith("Qm") and len(value) == 46:
        return bool(_CIDV0_RE.match(value))
    if value.startswith("bafy"):
        return bool(_CIDV1_BASE32_RE.match(value))
    return False


def extract_cids_from_text(text: str) -> list[str]:
    """
    Extract unique explicit CIDs from arbitrary text. Bounded dedup.

    GHOST_INVARIANTS:
        - No network I/O, no model/MLX load
        - Bounded: O(n) where n = word count, max ~1000 chars
        - Fail-safe: returns [] on any error
    """
    if not text:
        return []
    cids_seen: set[str] = set()
    cids: list[str] = []
    for word in text.split():
        word = word.strip().rstrip("/").rstrip(")")
        if has_explicit_cid(word) and word not in cids_seen:
            cids_seen.add(word)
            cids.append(word)
        # Also check for CID embedded in URL/path
        if "/" in word or ":" in word:
            for part in word.replace(":", "/").split("/"):
                part = part.strip()
                if has_explicit_cid(part) and part not in cids_seen:
                    cids_seen.add(part)
                    cids.append(part)
    return cids


__all__ = [
    "_URL_SCHEME_RE",
    "_URL_TRAILING_SLASH_RE",
    "_WILDCARD_RE",
    "_TRAILING_DOT_RE",
    "_IP_LIKE_RE",
    "_CIDV0_RE",
    "_CIDV1_BASE32_RE",
    "strip_url_scheme",
    "strip_trailing_slash",
    "strip_wildcard",
    "strip_trailing_dot",
    "is_wildcard",
    "is_ip_like",
    "has_explicit_cid",
    "extract_cids_from_text",
]
