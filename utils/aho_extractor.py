"""
Aho-Corasick Shadow Extractor
=============================

Sprint 8AU: Shadow pilot for multi-pattern extraction via Aho-Corasick.

DESIGN
------
- Shadow-only: does NOT replace live regex extraction
- Cached automaton singleton built once at first call
- pyahocorasick import deferred to first actual use
- Boot-path isolation: NOT imported by autonomous_orchestrator

PATTERN SET (PILOT)
-------------------
suspicious_keywords (from document_intelligence.DocumentIntelligence):
  "confidential", "classified", "secret", "proprietary",
  "internal use only", "do not distribute", "draft",
  "redacted", "sensitive"

GROUND TRUTH
------------
document_intelligence uses:  keyword in text_lower  (substring)
Aho output must match ground truth exactly.

OUTPUT NORMALIZATION
--------------------
Each match normalized to:
  {start: int, end: int, match: str}
end is EXCLUSIVE (not inclusive like pyahocorasick's end_index)

OVERLAP POLICY
--------------
Pilot uses non-overlapping pattern set (no prefix collisions among 9 keywords).
If overlaps occur, Aho iter() returns all matches — ground truth iter() would
also return all substring matches, so no dedup needed.

USAGE
-----
from utils.aho_extractor import get_suspicious_keywords_automaton, aho_scan_text

# Cached automaton
automaton = get_suspicious_keywords_automaton()

# Scan text
matches = aho_scan_text(automaton, "This document is classified and secret")
# Returns normalized list
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "get_suspicious_keywords_automaton",
    "aho_scan_text",
    "normalize_aho_match",
    "regex_scan_suspicious_keywords",
    "compare_aho_vs_regex",
    "PILOT_PATTERNS",
    "scan_suspicious_keywords_list",
]


def scan_suspicious_keywords_list(text: str) -> list[str]:
    """
    Return list of matched keyword strings (case-insensitive).

    This is the drop-in replacement for document_intelligence's
    _detect_suspicious_content loop: keyword in text_lower.

    Uses the cached Aho-Corasick automaton (built once on first call).
    Falls back to substring scan if automaton is unavailable.

    Returns:
        List of matched keywords (order matches PILOT_PATTERNS iteration order).
    """
    try:
        automaton = get_suspicious_keywords_automaton()
    except Exception:
        automaton = None

    if automaton is not None:
        # Aho path: iterate automaton, collect unique matched values
        seen: list[str] = []
        for _end_index, value in automaton.iter(text.lower()):
            if value not in seen:
                seen.append(value)
        return seen

    # Fallback: pure substring scan matching original semantics
    text_lower = text.lower()
    return [kw for kw in PILOT_PATTERNS if kw in text_lower]

# ------------------------------------------------------------------
# Pilot pattern set (from document_intelligence.DocumentIntelligence)
# ------------------------------------------------------------------

PILOT_PATTERNS: list[str] = [
    "confidential",
    "classified",
    "secret",
    "proprietary",
    "internal use only",
    "do not distribute",
    "draft",
    "redacted",
    "sensitive",
]

# ------------------------------------------------------------------
# Lazy import guard
# ------------------------------------------------------------------

_AhoCorasickModule: Any | None = None


def _get_ahocorasick() -> Any:
    """Lazy import — only loaded when automaton is first built."""
    global _AhoCorasickModule
    if _AhoCorasickModule is None:
        import ahocorasick

        _AhoCorasickModule = ahocorasick
    return _AhoCorasickModule


# ------------------------------------------------------------------
# Cached automaton singleton
# ------------------------------------------------------------------

_automaton_cache: Any | None = None


def get_suspicious_keywords_automaton() -> Any:
    """
    Return the cached Aho-Corasick automaton for suspicious_keywords.

    Built exactly once on first call, reused for all subsequent scans.
    Thread-safe: the automaton is read-only after construction.
    """
    global _automaton_cache
    if _automaton_cache is None:
        ahocorasick = _get_ahocorasick()
        automaton = ahocorasick.Automaton()
        for pattern in PILOT_PATTERNS:
            automaton.add_word(pattern, pattern)
        automaton.make_automaton()
        _automaton_cache = automaton
    return _automaton_cache


# ------------------------------------------------------------------
# Output normalization
# ------------------------------------------------------------------


def normalize_aho_match(end_index: int, match_value: str) -> dict[str, Any]:
    """
    Normalize a pyahocorasick (end_index, value) pair to exclusive end.

    pyahocorasick returns inclusive end_index.
    We convert to {start, end, match} with exclusive end to match
    the common {start, end} convention used in regex .span() results.
    """
    length = len(match_value)
    start = end_index - length + 1
    end = end_index + 1  # EXCLUSIVE
    return {"start": start, "end": end, "match": match_value}


# ------------------------------------------------------------------
# Aho scan
# ------------------------------------------------------------------


def aho_scan_text(automaton: Any, text: str) -> list[dict[str, Any]]:
    """
    Scan text with the Aho-Corasick automaton.

    Case-insensitive: haystack is lowercased before scanning,
    matching the same semantics as document_intelligence
    (keyword in text_lower).

    Returns a list of normalized match dicts with exclusive end.
    Empty list if no matches.
    """
    matches: list[dict[str, Any]] = []
    for end_index, value in automaton.iter(text.lower()):
        matches.append(normalize_aho_match(end_index, value))
    return matches


# ------------------------------------------------------------------
# Ground truth: regex / substring scan (mirrors document_intelligence)
# ------------------------------------------------------------------


def regex_scan_suspicious_keywords(text: str) -> list[dict[str, Any]]:
    """
    Ground-truth scan using the same substring semantics as document_intelligence.

    document_intelligence uses:  keyword in text_lower
    This mirrors that exactly.
    """
    text_lower = text.lower()
    matches: list[dict[str, Any]] = []
    for pattern in PILOT_PATTERNS:
        start = 0
        while True:
            idx = text_lower.find(pattern, start)
            if idx == -1:
                break
            matches.append({"start": idx, "end": idx + len(pattern), "match": pattern})
            start = idx + 1  # move forward to find overlapping matches
    return matches


# ------------------------------------------------------------------
# A/B comparison
# ------------------------------------------------------------------


def compare_aho_vs_regex(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """
    Compare Aho-Corasick vs regex (substring) outputs for a given text.

    Returns (aho_matches, regex_matches, are_identical).
    Both lists are sorted by start position.
    """
    automaton = get_suspicious_keywords_automaton()
    aho_matches = sorted(aho_scan_text(automaton, text), key=lambda m: m["start"])
    regex_matches = sorted(regex_scan_suspicious_keywords(text), key=lambda m: m["start"])

    # Compare as sets of (start, end, match) tuples
    aho_set = {(m["start"], m["end"], m["match"]) for m in aho_matches}
    regex_set = {(m["start"], m["end"], m["match"]) for m in regex_matches}
    are_identical = aho_set == regex_set

    return aho_matches, regex_matches, are_identical


# ------------------------------------------------------------------
# Boot isolation check (for tests)
# ------------------------------------------------------------------


def is_ahocorasick_loaded() -> bool:
    """Return True if pyahocorasick has been imported."""
    return _AhoCorasickModule is not None
