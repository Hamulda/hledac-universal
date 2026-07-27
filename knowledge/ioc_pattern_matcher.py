"""
knowledge/ioc_pattern_matcher.py — A5: Hot IOC Pattern Matcher
============================================================

Fast IOC pattern matching for hot patterns: BTC address, XMR address, Onion v3, email, URL.

Architecture:
- Python `re` for regex patterns (BTC, XMR, Onion v3, email, URL)
- Bounded: MAX_TEXT_SIZE limit, MAX_TEXT_BYTES per match
- fail-safe: any error → returns []
- always-on: no feature flag

Note: ahocorasick_rs supports literal-string multi-pattern matching.
For now, regex-based patterns are used for BTC/XMR/Onion (variable-length).
Architecture is ready for pyahocorasick when available.

M1 8GB: trivial CPU cost for small regex scans.

GHOST_INVARIANTS:
- fail-safe: any error → returns []
- bounded: MAX_TEXT_SIZE, per-match byte limits
- no blocking: pure Python re, no I/O
- always-on: no feature flag

Usage:
    from hledac.universal.knowledge.ioc_pattern_matcher import (
        IOCPatternMatcher,
        get_ioc_pattern_matcher,
    )
    matcher = get_ioc_pattern_matcher()
    matches = matcher.match("payment to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa BTC")
    # → [IOCMatch(pattern_name="btc_address", matched_value="1A1zP1eP5QGefi...", start=12, end=46)]
"""


import logging
import re
from dataclasses import dataclass
import msgspec
from typing import Final

__all__ = [
    "IOCPatternMatcher",
    "get_ioc_pattern_matcher",
    "IOCMatch",
]

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_TEXT_SIZE: Final[int] = 10_000_000  # max text to process (10MB)
MAX_MATCH_VALUE: Final[int] = 256  # max characters per matched value

# ── Hot IOC Patterns ──────────────────────────────────────────────────────────
# Compiled once at module load; regex patterns matched via re.finditer()

BTC_RE = re.compile(r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}")
XMR_RE = re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b|\b8[1-9A-HJ-NP-Za-km-z]{95}\b")
ONION_V3_RE = re.compile(r"\b[a-z2-7]{56}\.onion\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\"']+")

HOT_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("btc_address", BTC_RE),
    ("xmr_address", XMR_RE),
    ("onion_v3", ONION_V3_RE),
    ("email", EMAIL_RE),
    ("url", URL_RE),
]


class IOCMatch(msgspec.Struct, frozen=True, gc=False):
    """A single IOC pattern match."""
    pattern_name: str
    matched_value: str
    start: int
    end: int


class IOCPatternMatcher:
    """
    Fast IOC pattern matcher for hot patterns.

    Uses compiled Python regex patterns for O(n) multi-pattern scanning.
    Patterns: BTC address, XMR address, Onion v3, email, URL.

    Thread-safe: all regex patterns are module-level compiled constants.
    """

    __slots__ = ()

    def match(self, text: str) -> list[IOCMatch]:
        """
        Find all IOC pattern matches in text.

        Args:
            text: Input text to scan (max 10MB)

        Returns:
            List of IOCMatch objects, ordered by start position.
            Returns [] on any error.
        """
        if not text:
            return []
        if len(text) > MAX_TEXT_SIZE:
            text = text[:MAX_TEXT_SIZE]

        try:
            matches: list[IOCMatch] = []
            seen: set[int] = set()  # deduplicate overlapping matches

            for name, pattern in HOT_PATTERNS:
                for m in pattern.finditer(text):
                    if m.start() in seen:
                        continue
                    seen.add(m.start())
                    value = m.group(0)
                    if len(value) > MAX_MATCH_VALUE:
                        value = value[:MAX_MATCH_VALUE]
                    matches.append(IOCMatch(
                        pattern_name=name,
                        matched_value=value,
                        start=m.start(),
                        end=m.end(),
                    ))

            matches.sort(key=lambda x: x.start)
            return matches
        except Exception as exc:
            logger.debug("IOCPatternMatcher: match error: %s", exc)
            return []

    @property
    def pattern_count(self) -> int:
        """Number of patterns."""
        return len(HOT_PATTERNS)


# ── Module-level singleton ─────────────────────────────────────────────────────

_matcher: IOCPatternMatcher | None = None


def get_ioc_pattern_matcher() -> IOCPatternMatcher:
    """Get the module-level IOCPatternMatcher singleton."""
    global _matcher
    if _matcher is None:
        _matcher = IOCPatternMatcher()
    return _matcher
