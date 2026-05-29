"""
pipeline/scoring.py — Shared scoring utilities for live public and feed pipelines.

Sprint F222: Extract shared scoring/normalization logic from live_public_pipeline.py
and live_feed_pipeline.py into a single module. Both pipelines import from it.

Invariant: scoring logic lives in ONE place. Tests for scoring live in one place.
"""

from __future__ import annotations

import html
import re

# ---------------------------------------------------------------------------
# Feed Entry Quality Signal
# ---------------------------------------------------------------------------

# Minimum content length that qualifies as "substantive" for quality scoring
_MIN_SUBSTANTIVE_CHARS: int = 80

# Char-length thresholds for entry quality bands
_QUALITY_TITLE_ONLY_CHARS: int = 60

# Language mismatch bonus — feed language vs common OSINT target languages
_OSINT_RELEVANT_LANGUAGES: frozenset[str] = frozenset({"en", "cs", "sk", "de", "pl"})

# Sprint 8BE: markdownify lazy import (optional dependency)
_markdownify_available: bool = False
try:
    import markdownify  # noqa: F401
    _markdownify_available = True
except ImportError:
    markdownify = None  # type: ignore[assignment]


class EntryQualitySignal:
    """
    Lightweight quality signal for a single entry.
    Used for routing decisions and observability — NOT for filtering findings.
    """
    __slots__ = (
        "quality_band",
        "quality_score",
        "quality_reason_tag",
        "metadata_boost",
        "language_mismatch",
    )

    def __init__(
        self,
        quality_band: str = "unknown",
        quality_score: int = 0,
        quality_reason_tag: str = "",
        metadata_boost: bool = False,
        language_mismatch: bool = False,
    ) -> None:
        self.quality_band = quality_band
        self.quality_score = quality_score
        self.quality_reason_tag = quality_reason_tag
        self.metadata_boost = metadata_boost
        self.language_mismatch = language_mismatch

    def __repr__(self) -> str:
        return (
            f"EntryQualitySignal(band={self.quality_band!r}, score={self.quality_score}, "
            f"tag={self.quality_reason_tag!r}, boost={self.metadata_boost}, "
            f"mismatch={self.language_mismatch})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EntryQualitySignal):
            return NotImplemented
        return (
            self.quality_band == other.quality_band
            and self.quality_score == other.quality_score
            and self.quality_reason_tag == other.quality_reason_tag
            and self.metadata_boost == other.metadata_boost
            and self.language_mismatch == other.language_mismatch
        )


def _compute_entry_quality_signal(
    title: str,
    summary: str,
    rich_content: str,
    entry_author: str,
    feed_title: str,
    feed_language: str,
    adapter_quality_score: float | None = None,
) -> EntryQualitySignal:
    """
    Compute lightweight quality signal from entry metadata.

    No LLM. No new model. Pure heuristic.
    """
    # Measure raw text substance
    title_len = len(title.strip()) if title else 0
    summary_len = len(summary.strip()) if summary else 0
    rich_len = len(rich_content.strip()) if rich_content else 0

    # Determine content substance
    has_rich = rich_len >= _MIN_SUBSTANTIVE_CHARS
    has_summary = summary_len >= _MIN_SUBSTANTIVE_CHARS
    has_author = bool(entry_author and len(entry_author.strip()) >= 2)
    has_feed_title = bool(feed_title and len(feed_title.strip()) >= 2)

    # Language assessment
    lang_mismatch = False
    if feed_language:
        lang_lower = feed_language.strip().lower()[:2]  # ISO 639-1 prefix
        lang_mismatch = lang_lower not in _OSINT_RELEVANT_LANGUAGES

    # Compute quality score (0-100)
    score = 0

    # Base: text substance
    if has_rich:
        score += 40
    elif has_summary:
        score += 20

    if title_len > _QUALITY_TITLE_ONLY_CHARS:
        score += 10

    # Metadata boosts
    metadata_boost = False
    reason_tags: list[str] = []

    if has_author:
        score += 15
        metadata_boost = True
        reason_tags.append("author_present")

    if has_feed_title:
        score += 10
        metadata_boost = True
        reason_tags.append("feed_title_context")

    if not lang_mismatch and feed_language:
        score += 10
        reason_tags.append("language_match")

    # Clamp score
    score = min(score, 100)

    # Quality band
    if has_rich or (has_summary and score >= 50):
        band = "high"
    elif score >= 30:
        band = "medium"
    elif score >= 10:
        band = "low"
    else:
        band = "unknown"

    if not reason_tags:
        if title_len > 0:
            reason_tags.append("title_only")
        else:
            reason_tags.append("no_content")

    # F192D DF-2 FIX (cascading bug): original_band preserves the initial band
    # for all adapter downgrade checks. Without this, reassigning `band` between
    # if/elif branches causes cascading downgrade (high→medium→low→unknown
    # instead of just high→medium).
    original_band = band
    final_band = band
    if adapter_quality_score is not None and adapter_quality_score < 0.3:
        # Adapter detected spam/low-quality content — downgrade band
        # Use original_band to avoid cascading reassignment bug
        if original_band == "high":
            final_band = "medium"
        elif original_band == "medium":
            final_band = "low"
        elif original_band == "low":
            final_band = "unknown"
        reason_tags.append("adapter_low_quality")

    return EntryQualitySignal(
        quality_band=final_band,
        quality_score=score,
        quality_reason_tag=",".join(reason_tags),
        metadata_boost=metadata_boost,
        language_mismatch=lang_mismatch,
    )


# ---------------------------------------------------------------------------
# Feed HTML text processing utilities
# ---------------------------------------------------------------------------

# Match entire <script>...</script> or <style>...</style> blocks (DOTALL)
_SCRIPT_STYLE_RE = re.compile(
    r"<script[^>]*>.*?</script>|"
    r"<style[^>]*>.*?</style>",
    re.DOTALL | re.IGNORECASE,
)
# Replace any HTML tag with a single space
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_MULTI_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")

_RICH_CONTENT_MIN_CHARS: int = 40


def _strip_html_tags_from_text(text: str) -> str:
    """
    Strip HTML tags word-boundary safe, OSINT-safe.

    Steps (strict order per invariant B.9):
    1. Remove entire <script> and <style> blocks
    2. Replace remaining HTML tags with a single space
    3. Normalize whitespace
    4. html.unescape AFTER tag removal
    """
    if not text:
        return ""
    if not isinstance(text, str):
        return ""
    # Step 1: Remove script/style blocks completely
    cleaned = _SCRIPT_STYLE_RE.sub("", text)
    # Step 2: Replace tags with space
    cleaned = _STRIP_TAGS_RE.sub(" ", cleaned)
    # Step 3: Normalize whitespace
    cleaned = _MULTI_WHITESPACE_RE.sub(" ", cleaned).strip()
    # Step 4: Unescape HTML entities AFTER tag removal
    cleaned = html.unescape(cleaned)
    return cleaned


def _convert_rich_html_to_text(rich_html: str) -> str:
    """
    Convert rich HTML content to clean text.

    Priority (per Sprint 8BE Phase 1):
    1. markdownify (if available) — preserves structure
    2. strip fallback — same as summary path

    Returns empty string if input is empty/whitespace.
    """
    if not rich_html or not rich_html.strip():
        return ""
    if _markdownify_available:
        try:
            import markdownify as mf

            converted = mf.markdownify(rich_html, strip=["script", "style"])
            converted = _MULTI_WHITESPACE_RE.sub(" ", converted).strip()
            if converted:
                return converted
        except Exception:
            pass
    return _strip_html_tags_from_text(rich_html)


# ---------------------------------------------------------------------------
# Assembly substance tiers (used to diagnose WHERE signal is lost)
# ---------------------------------------------------------------------------

# Assembly substance tiers — used to diagnose WHERE signal is lost
# in the feed-native assembly phase
ASSEMBLY_TIER_NO_CONTENT: int = 0
ASSEMBLY_TIER_TITLE_ONLY: int = 1
ASSEMBLY_TIER_SUMMARY_ONLY: int = 2
ASSEMBLY_TIER_RICH_CONTENT: int = 3


def _classify_assembly_substance(
    title: str,
    summary: str,
    rich_content: str,
) -> tuple[str, int]:
    """
    Classify how much substantive content was assembled from feed-native sources.

    Returns (tier_name, tier_level):
      "no_content"       — nothing assembled (sentinel only)
      "title_only"       — title only, no meaningful body
      "summary_only"     — summary assembled but no rich_content
      "rich_content"     — rich HTML content was available and used

    This replaces the implicit "[no content]" sentinel check.
    Tier level is used for ordering (higher = more substantive).
    """
    has_title = bool(title and title.strip())
    has_summary = bool(summary and summary.strip())
    has_rich = bool(rich_content)

    if has_rich:
        converted = _convert_rich_html_to_text(rich_content)
        if converted and len(converted) >= _RICH_CONTENT_MIN_CHARS:
            return ("rich_content", ASSEMBLY_TIER_RICH_CONTENT)

    if has_summary:
        stripped = _strip_html_tags_from_text(summary)
        if stripped and len(stripped.strip()) >= _MIN_SUBSTANTIVE_CHARS:
            return ("summary_only", ASSEMBLY_TIER_SUMMARY_ONLY)

    if has_title:
        title_len = len(title.strip())
        if title_len >= _QUALITY_TITLE_ONLY_CHARS:
            return ("title_only", ASSEMBLY_TIER_TITLE_ONLY)
        elif title_len > 0:
            return ("title_only", ASSEMBLY_TIER_TITLE_ONLY)

    return ("no_content", ASSEMBLY_TIER_NO_CONTENT)


# ---------------------------------------------------------------------------
# Deterministic clean text assembly
# ---------------------------------------------------------------------------


def _assemble_enriched_feed_text(
    title: str,
    summary: str,
    rich_content: str,
    feed_title: str = "",
    entry_author: str = "",
) -> tuple[str, str]:
    """
    Assemble deterministic clean text from title + summary + rich_content + metadata.

    Sprint 8BE PHASE 1 + F150H: source-specific text enrichment with
    corrected priority so rich HTML content is used as primary surface.
    Metadata (feed_title, entry_author) are prepended as lightweight context anchors.

    Priority hierarchy:
    1. feed_title + author as metadata context header (if available)
    2. rich_content (converted, if substantive — HTML articles etc.)
    3. summary (stripped and cleaned, if non-empty)
    4. title (as final anchor when nothing else available)
    5. sentinel "[no content]" if all empty

    Returns (clean_text, enrichment_phase).
    """
    parts: list[str] = []
    enrichment_phase = "none"

    # Type guards: ensure we have real strings, not MagicMock or other objects
    if not isinstance(feed_title, str):
        feed_title = ""
    if not isinstance(entry_author, str):
        entry_author = ""

    # Priority 0: metadata context header — feed_title and author as lightweight anchors
    # These are prepended at the top so PatternMatcher sees them first
    # Bounded: only add if they provide genuine context beyond the title
    meta_parts: list[str] = []
    if feed_title and feed_title.strip():
        ft = feed_title.strip()
        if not isinstance(ft, str):
            ft = ""
        if ft and ft != title.strip():  # avoid duplicating title
            meta_parts.append(ft)
    if entry_author and entry_author.strip() and len(entry_author.strip()) >= 2:
        ea = entry_author.strip()
        if not isinstance(ea, str):
            ea = ""
        # Only add author if not already embedded in title
        if ea and ea.lower() not in title.lower():
            meta_parts.append(f"by {ea}")
    if meta_parts:
        parts.append(" | ".join(meta_parts))

    # Priority 1: rich_content first — full HTML articles from content:encoded / Atom content
    # Only use converted text if it's substantive (avoids noise from tiny HTML fragments)
    if rich_content:
        converted = _convert_rich_html_to_text(rich_content)
        if converted and len(converted) >= _RICH_CONTENT_MIN_CHARS:
            parts.append(converted)
            enrichment_phase = "feed_rich_content"

    # Priority 2: title + summary — title as anchor, summary as secondary context
    # Only include title if we have something richer below; title alone is not enough
    # for substantive pattern matching, so it stays as anchor until we confirm
    # we have rich_content/summary that covers the signal
    if title:
        parts.append(title.strip())

    if summary:
        stripped = _strip_html_tags_from_text(summary)
        if stripped:
            parts.append(stripped)

    if not parts:
        return ("[no content]", "none")
    return ("\n\n".join(parts), enrichment_phase)


def _assemble_clean_feed_text(title: str, summary: str) -> str:
    """
    Assemble deterministic clean text from title + summary.

    Deterministic assembly order:
    1. title (if non-empty)
    2. summary (stripped and cleaned, if non-empty)
    3. sentinel "[no content]" if both empty

    No html.unescape before tag stripping (per B.9).
    """
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if summary:
        stripped = _strip_html_tags_from_text(summary)
        if stripped:
            parts.append(stripped)
    if not parts:
        return "[no content]"
    return "\n\n".join(parts)


# Backwards-compatible alias (used by probe_8ah tests)
_entry_payload_text = _assemble_clean_feed_text
