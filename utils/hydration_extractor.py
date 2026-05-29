"""
Static hydration extractor for SPA/JS-heavy pages.

Reduces need for full JS rendering on M1 8GB by extracting pre-existing
hydration data already present in the HTML source (Next.js, Nuxt, generic
hydration, JSON-LD, and metadata).

All operations are:
- Bounded: max 2MB input, output limits, no DOM rendering
- Fail-soft: no exceptions escape, malformed input → graceful degradation
- Async-agnostic: pure synchronous functions, no network calls
"""

from __future__ import annotations

import re
from typing import Final

import msgspec
import orjson

# ---------------------------------------------------------------------------
# Bounds (M1 8GB safe)
# ---------------------------------------------------------------------------

MAX_HTML_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MB input cap
MAX_EXTRACTED_TEXT: Final[int] = 100_000       # 100 KB output cap
MAX_JSON_LD_BLOCKS: Final[int] = 10             # max JSON-LD script blocks
MAX_JSON_DEPTH: Final[int] = 20                # max traversal depth
MAX_SCRIPT_LEN: Final[int] = 500_000           # 500 KB per script block
MAX_TITLE_LEN: Final[int] = 500                 # max title chars
MAX_METADATA_LEN: Final[int] = 2000             # max metadata dict serialized
MAX_CANDIDATE_LEN: Final[int] = 50_000          # max single JSON candidate text

# ---------------------------------------------------------------------------
# Reason constants (telemetry)
# ---------------------------------------------------------------------------

_REASON_SUFFICIENT_NEXT = "next_data_sufficient"
_REASON_SUFFICIENT_NUXT = "nuxt_data_sufficient"
_REASON_SUFFICIENT_JSON_LD = "json_ld_sufficient"
_REASON_SUFFICIENT_METADATA = "metadata_sufficient"
_REASON_FOUND_INSUFFICIENT = "hydration_found_but_insufficient"
_REASON_NONE = "no_hydration_found"
# Reserved for future telemetry (not currently emitted by extract_static_hydration):
# _REASON_PARSE_ERROR = "parse_error"
# _REASON_MAX_BYTES = "max_bytes_exceeded"

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class HydrationExtractionResult(msgspec.Struct, gc=False):
    """
    Result of static hydration extraction from HTML.

    Attributes
    ----------
    found : bool
        True if any hydration data was located in the HTML.
    sufficient : bool
        True if the found data is rich enough to skip JS rendering.
    sources : tuple[str, ...]
        Which extraction sources produced content (e.g. "next_data", "nuxt_data").
    text : str
        Extracted meaningful text content (title + body/description).
    metadata : dict[str, object]
        Structured metadata: title, description, canonical, og:*, JSON-LD types,
        extracted links (canonical, RSS, Atom).
    reason : str | None
        Telemetry reason string for logging/analytics.
    """

    found: bool
    sufficient: bool
    sources: tuple[str, ...] = ()
    text: str = ""
    metadata: dict[str, object] = {}
    reason: str | None = None
    # Added in F214Z — Hydration scoring & telemetry
    hydration_score: float = 0.0  # 0.0–1.0, conservative scoring
    quality_signals: tuple[str, ...] = ()  # e.g. "title", "body", "json_ld_article"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Next.js __NEXT_DATA__
_RE_NEXT_DATA: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Nuxt __NUXT_DATA__ (SSR rendered)
_RE_NUXT_DATA: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]*>(?:window\.)?__NUXT_DATA__\s*=\s*(\[.*?\]);?\s*</script>',
    re.DOTALL | re.IGNORECASE,
)

# Nuxt window.__NUXT__
_RE_NUXT_GLOBAL: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]*>window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>',
    re.DOTALL | re.IGNORECASE,
)

# Generic hydration
_RE_INITIAL_STATE: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]*>(?:window\.)?__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>',
    re.DOTALL | re.IGNORECASE,
)
_RE_PRELOADED_STATE: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]*>(?:window\.)?__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>',
    re.DOTALL | re.IGNORECASE,
)
_RE_APOLLO_STATE: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]*>(?:window\.)?__APOLLO_STATE__\s*=\s*(\{.*?\});?\s*</script>',
    re.DOTALL | re.IGNORECASE,
)

# JSON-LD
_RE_JSON_LD: Final[re.Pattern[str]] = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Metadata
_RE_CANONICAL: Final[re.Pattern[str]] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*canonical[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_RSS: Final[re.Pattern[str]] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*alternate[^"\']*["\'][^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_ATOM: Final[re.Pattern[str]] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*alternate[^"\']*["\'][^>]+type=["\']application/atom\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_TITLE: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_DESC: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_META_DESC: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_TITLE_TAG: Final[re.Pattern[str]] = re.compile(
    r'<title[^>]*>(.*?)</title>',
    re.DOTALL | re.IGNORECASE,
)
_RE_OG_IMAGE: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_URL: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_ARTICLE_PUBLISHED: Final[re.Pattern[str]] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Content types that signal rich data
# ---------------------------------------------------------------------------

_CONTENT_TYPES: Final[frozenset[str]] = frozenset({
    "Article",
    "NewsArticle",
    "BlogPosting",
    "Person",
    "Organization",
    "WebSite",
    "BreadcrumbList",
    "Product",
    "Event",
})

# Minimum lengths for sufficiency heuristic
_MIN_TITLE_LEN: Final[int] = 15
_MIN_BODY_LEN: Final[int] = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _safe_json_parse(raw: str) -> dict | None:
    """Fail-soft JSON parse — never raises, returns None on error."""
    try:
        # Reject obviously too-large inputs before parsing
        if len(raw) > MAX_SCRIPT_LEN:
            return None
        return orjson.loads(raw)
    except Exception:
        return None


def _flatten_text(obj, depth: int = 0, seen: set[int] | None = None) -> str:
    """
    Recursively extract text from a parsed JSON object.
    Handles cycles, depth limit, and size cap.
    """
    if depth > MAX_JSON_DEPTH:
        return ""
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return ""
    seen.add(obj_id)

    if isinstance(obj, str):
        text = obj.strip()
        if 2 < len(text) < MAX_CANDIDATE_LEN:
            return text
        return ""
    if isinstance(obj, list):
        parts = []
        for item in obj:
            text = _flatten_text(item, depth + 1, seen)
            if text:
                parts.append(text)
            if sum(len(p) for p in parts) > MAX_EXTRACTED_TEXT:
                break
        return " ".join(parts)
    if isinstance(obj, dict):
        # Fields that usually contain meaningful content
        CONTENT_FIELDS = (
            "props", "pageProps", "serverData", "data", "body", "content",
            "text", "html", "result", "articleBody", "description", "headline",
        )
        parts = []
        for key in CONTENT_FIELDS:
            if key in obj:
                text = _flatten_text(obj[key], depth + 1, seen)
                if text:
                    parts.append(text)
        # Also try top-level keys as fallback
        if not parts:
            for v in obj.values():
                if isinstance(v, str) and 2 < len(v.strip()) < MAX_CANDIDATE_LEN:
                    parts.append(v.strip())
                elif isinstance(v, dict):
                    text = _flatten_text(v, depth + 1, seen)
                    if text:
                        parts.append(text)
        return " ".join(parts)
    return ""


def _extract_from_script(html: str, pattern: re.Pattern) -> str | None:
    """Extract JSON string content from first matching script tag."""
    match = pattern.search(html)
    if not match:
        return None
    json_str = match.group(1).strip()
    if not json_str or len(json_str) > MAX_SCRIPT_LEN:
        return None
    # Strip trailing whitespace and extra closing braces that may appear
    # due to HTML script tag closing conventions (e.g., `...</script>`)
    json_str = json_str.rstrip()
    while json_str and json_str[-1] == "}" and json_str.count("{") < json_str.count("}"):
        json_str = json_str[:-1].rstrip()
    if not json_str:
        return None
    return json_str


def _json_ld_types(parsed: dict | list, found_types: list[str]) -> None:
    """Recursively collect @type values from JSON-LD structure."""
    if isinstance(parsed, dict):
        typ = parsed.get("@type", "")
        if typ:
            if isinstance(typ, list):
                found_types.extend(str(t) for t in typ)
            else:
                found_types.append(str(typ))
        for val in parsed.values():
            _json_ld_types(val, found_types)
    elif isinstance(parsed, list):
        for item in parsed:
            _json_ld_types(item, found_types)


def _has_meaningful_title(info: dict) -> bool:
    """Check if info dict has a meaningful title >= MIN_TITLE_LEN."""
    title = info.get("title", "") or info.get("og_title", "")
    return bool(title and len(title) >= _MIN_TITLE_LEN)


def _has_meaningful_body(info: dict) -> bool:
    """Check if info dict has meaningful body/description >= MIN_BODY_LEN."""
    body = (
        info.get("body", "") or info.get("description", "") or
        info.get("json_ld_text", "") or info.get("meta_desc", "")
    )
    return bool(body and len(body) >= _MIN_BODY_LEN)


def _has_content_json_ld(info: dict) -> bool:
    """Check if info has JSON-LD type from CONTENT_TYPES."""
    return bool(info.get("json_ld_types") and any(
        t in _CONTENT_TYPES for t in info.get("json_ld_types", [])
    ))


def _has_metadata_signal(info: dict) -> bool:
    """Check if info has canonical/feed/alternate links."""
    metadata = info.get("metadata", {})
    return bool(
        metadata.get("canonical") or
        metadata.get("rss") or
        metadata.get("atom")
    )


def _compute_hydration_score(info: dict, input_truncated: bool = False) -> tuple[float, tuple[str, ...]]:
    """
    Compute conservative hydration quality score (0.0–1.0).

    Scoring rules (conservative):
    - title/headline found: +0.2
    - meaningful description/body: +0.3
    - JSON-LD Article/NewsArticle/BlogPosting: +0.3
    - canonical URL: +0.1
    - feed/alternate RSS/Atom: +0.1
    - Next/Nuxt/generic hydration payload with content-like fields: +0.4
    - truncated input: penalize
    - very short extracted text: penalize

    Returns (score, quality_signals).
    """
    signals: list[str] = []
    score = 0.0

    has_title = _has_meaningful_title(info)
    if has_title:
        score += 0.2
        signals.append("title")

    has_body = _has_meaningful_body(info)
    if has_body:
        score += 0.3
        signals.append("body")

    has_json_ld_content = _has_content_json_ld(info)
    if has_json_ld_content:
        score += 0.3
        signals.append("json_ld_article")
        # Add specific JSON-LD types as signals
        for t in info.get("json_ld_types", []):
            if t in _CONTENT_TYPES:
                signals.append(f"json_ld_{t.lower()}")

    metadata = info.get("metadata", {})
    if metadata.get("canonical"):
        score += 0.1
        signals.append("canonical")
    if metadata.get("rss") or metadata.get("atom"):
        score += 0.1
        signals.append("feed_alternate")

    # Hydration payload with content (Next/Nuxt/generic with body)
    body_source = info.get("_body_source", "")
    if body_source in (_REASON_SUFFICIENT_NEXT, _REASON_SUFFICIENT_NUXT, _REASON_SUFFICIENT_METADATA):
        if info.get("body"):
            score += 0.4
            signals.append("hydration_payload")

    # Penalties
    if input_truncated:
        score -= 0.2
        signals.append("input_truncated")

    total_text = info.get("body", "") or info.get("description", "") or info.get("json_ld_text", "")
    if total_text and len(total_text) < 100:
        score -= 0.1
        signals.append("short_text")

    return (max(0.0, min(1.0, score)), tuple(signals))


def _is_sufficient(info: dict) -> tuple[bool, str]:
    """
    Conservative sufficiency check.
    Returns (sufficient, reason_str).
    """
    has_title = _has_meaningful_title(info)
    has_body = _has_meaningful_body(info)
    has_json_ld_content = _has_content_json_ld(info)
    has_meta_signal = _has_metadata_signal(info)

    if has_title and (has_body or has_meta_signal):
        if has_json_ld_content:
            return True, _REASON_SUFFICIENT_JSON_LD
        if has_body:
            # Title + body is sufficient
            return True, info.get("_body_source", _REASON_SUFFICIENT_METADATA)
        return True, _REASON_SUFFICIENT_METADATA
    if has_json_ld_content and (has_title or has_meta_signal):
        return True, _REASON_SUFFICIENT_JSON_LD
    if has_title and has_meta_signal:
        return True, _REASON_SUFFICIENT_METADATA

    return False, ""


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def extract_static_hydration(
    html: str,
    *,
    max_bytes: int = MAX_HTML_BYTES,
) -> HydrationExtractionResult:
    """
    Extract pre-rendered hydration data from an HTML string.

    Looks for: Next.js __NEXT_DATA__, Nuxt __NUXT_DATA__/window.__NUXT__,
    generic hydration (__INITIAL_STATE__, __PRELOADED_STATE__, __APOLLO_STATE__),
    JSON-LD blocks, and metadata (canonical, og:*, RSS/Atom).

    Bounded: HTML larger than max_bytes is truncated first.
    Fail-soft: returns result with found=False on any parsing error.

    Parameters
    ----------
    html : str
        Raw HTML string from HTTP response.
    max_bytes : int
        Maximum HTML bytes to process (default 2 MB).
        Input larger than this is truncated before parsing.

    Returns
    -------
    HydrationExtractionResult
        Typed result with found/sufficient/sources/text/metadata/reason.
        Always returns a result — never raises.
    """
    # Fast path: empty or way too short
    if not html or len(html) < 50:
        return HydrationExtractionResult(
            found=False,
            sufficient=False,
            sources=(),
            text="",
            metadata={},
            reason=_REASON_NONE,
        )

    # Bounds: truncate oversized input
    input_truncated = False
    if len(html) > max_bytes:
        html = html[:max_bytes]
        input_truncated = True

    sources: list[str] = []
    info: dict = {
        "title": "",
        "og_title": "",
        "body": "",
        "description": "",
        "meta_desc": "",
        "json_ld_text": "",
        "json_ld_types": [],
        "metadata": {},
    }

    # ---- Extract JSON-based hydration ----

    # Next.js
    raw = _extract_from_script(html, _RE_NEXT_DATA)
    if raw:
        parsed = _safe_json_parse(raw)
        if parsed is not None:
            sources.append("next_data")
            text = _flatten_text(parsed)
            if text:
                info["body"] = _truncate(text, MAX_EXTRACTED_TEXT)
            # Extract title from props.pageProps or top-level
            title = (
                parsed.get("props", {}).get("pageProps", {}).get("title", "") or
                parsed.get("props", {}).get("pageProps", {}).get("serverData", {}).get("title", "") or
                parsed.get("props", {}).get("pageProps", {}).get("data", {}).get("title", "") or
                parsed.get("pageProps", {}).get("title", "") or
                parsed.get("title", "") or
                ""
            )
            if title:
                info["title"] = _truncate(str(title), MAX_TITLE_LEN)
            info["_body_source"] = _REASON_SUFFICIENT_NEXT

    # Nuxt __NUXT_DATA__
    if not sources or not info.get("body"):
        raw = _extract_from_script(html, _RE_NUXT_DATA)
        if raw:
            parsed = _safe_json_parse(raw)
            if parsed is not None:
                sources.append("nuxt_data")
                text = _flatten_text(parsed)
                if text:
                    info["body"] = _truncate(text, MAX_EXTRACTED_TEXT)
                title = (
                    parsed[0].get("data", {}).get("title", "") if parsed and isinstance(parsed, list) else
                    parsed.get("title", "") or
                    ""
                )
                if title:
                    info["title"] = _truncate(str(title), MAX_TITLE_LEN)
                info["_body_source"] = _REASON_SUFFICIENT_NUXT

    # Nuxt window.__NUXT__
    if not info.get("body"):
        raw = _extract_from_script(html, _RE_NUXT_GLOBAL)
        if raw:
            parsed = _safe_json_parse(raw)
            if parsed is not None:
                sources.append("nuxt_data")
                text = _flatten_text(parsed)
                if text:
                    info["body"] = _truncate(text, MAX_EXTRACTED_TEXT)
                title = parsed.get("title", "") or parsed.get("data", {}).get("title", "") or ""
                if title:
                    info["title"] = _truncate(str(title), MAX_TITLE_LEN)
                info["_body_source"] = _REASON_SUFFICIENT_NUXT

    # Generic hydration patterns
    generic_patterns = [
        ("initial_state", _RE_INITIAL_STATE),
        ("preloaded_state", _RE_PRELOADED_STATE),
        ("apollo_state", _RE_APOLLO_STATE),
    ]
    for name, pattern in generic_patterns:
        if not info.get("body"):
            raw = _extract_from_script(html, pattern)
            if raw:
                parsed = _safe_json_parse(raw)
                if parsed is not None:
                    sources.append(name)
                    text = _flatten_text(parsed)
                    if text:
                        info["body"] = _truncate(text, MAX_EXTRACTED_TEXT)
                    # Try to extract title from common nested structures
                    title_candidates = [
                        parsed.get("props", {}).get("page", {}).get("title", ""),
                        parsed.get("props", {}).get("pageProps", {}).get("title", ""),
                        parsed.get("serverData", {}).get("title", ""),
                        parsed.get("data", {}).get("title", ""),
                        parsed.get("ROOT_QUERY", {}).get("title", ""),
                        parsed.get("title", ""),
                    ]
                    for t in title_candidates:
                        if t and len(str(t)) >= _MIN_TITLE_LEN:
                            info["title"] = _truncate(str(t), MAX_TITLE_LEN)
                            break
                    info["_body_source"] = _REASON_SUFFICIENT_METADATA

    # ---- JSON-LD extraction ----
    json_ld_types: list[str] = []
    json_ld_texts: list[str] = []
    for i, match in enumerate(_RE_JSON_LD.finditer(html)):
        if i >= MAX_JSON_LD_BLOCKS:
            break
        raw = match.group(1).strip()
        if len(raw) > MAX_SCRIPT_LEN:
            continue
        parsed = _safe_json_parse(raw)
        if parsed is not None:
            _json_ld_types(parsed, json_ld_types)
            text = _flatten_text(parsed)
            if text:
                json_ld_texts.append(text)

    if json_ld_types:
        sources.append("json_ld")
        info["json_ld_types"] = json_ld_types
        info["json_ld_text"] = _truncate(" ".join(json_ld_texts), MAX_EXTRACTED_TEXT)
        if not info.get("body") and json_ld_texts:
            info["body"] = info["json_ld_text"]
        # Promote JSON-LD headline/name to title if no title yet
        if not info.get("title"):
            for match in _RE_JSON_LD.finditer(html):
                raw = match.group(1).strip()
                if len(raw) > MAX_SCRIPT_LEN:
                    continue
                parsed = _safe_json_parse(raw)
                if parsed is not None:
                    headline = parsed.get("headline") or parsed.get("name") or ""
                    if headline and len(str(headline)) >= _MIN_TITLE_LEN:
                        info["title"] = _truncate(str(headline), MAX_TITLE_LEN)
                        break

    # ---- Metadata extraction ----
    metadata: dict[str, object] = {}

    def _meta_val(pattern: re.Pattern, key: str):
        m = pattern.search(html)
        if m:
            metadata[key] = m.group(1).strip()

    _meta_val(_RE_CANONICAL, "canonical")
    _meta_val(_RE_RSS, "rss")
    _meta_val(_RE_ATOM, "atom")
    _meta_val(_RE_OG_TITLE, "og_title")
    _meta_val(_RE_OG_DESC, "og_description")
    _meta_val(_RE_META_DESC, "meta_description")
    _meta_val(_RE_TITLE_TAG, "title_tag")
    _meta_val(_RE_OG_IMAGE, "og_image")
    _meta_val(_RE_OG_URL, "og_url")
    _meta_val(_RE_ARTICLE_PUBLISHED, "article_published_time")

    # Copy into info
    if not info.get("title"):
        info["title"] = metadata.get("og_title", "") or metadata.get("title_tag", "") or ""
    if not info.get("description"):
        info["description"] = metadata.get("og_description", "") or metadata.get("meta_description", "") or ""
    if not info.get("meta_desc"):
        info["meta_desc"] = metadata.get("meta_description", "") or ""

    info["metadata"] = metadata

    if not sources and not metadata:
        return HydrationExtractionResult(
            found=False,
            sufficient=False,
            sources=(),
            text="",
            metadata={},
            reason=_REASON_NONE,
            hydration_score=0.0,
            quality_signals=(),
        )

    found = len(sources) > 0 or bool(metadata)

    # ---- Compute score (before sufficiency check) ----
    hydration_score, quality_signals = _compute_hydration_score(info, input_truncated)

    # ---- Sufficiency check ----
    if found:
        sufficient, reason = _is_sufficient(info)
        if sufficient:
            # Build composite text: title + body
            parts = []
            if info.get("title"):
                parts.append(info["title"])
            body = info.get("body") or info.get("description", "")
            if body:
                parts.append(body)
            final_text = _truncate(" | ".join(parts), MAX_EXTRACTED_TEXT)

            return HydrationExtractionResult(
                found=True,
                sufficient=True,
                sources=tuple(sources),
                text=final_text,
                metadata=metadata,
                reason=reason,
                hydration_score=hydration_score,
                quality_signals=quality_signals,
            )
        else:
            return HydrationExtractionResult(
                found=True,
                sufficient=False,
                sources=tuple(sources),
                text="",
                metadata=metadata,
                reason=_REASON_FOUND_INSUFFICIENT,
                hydration_score=hydration_score,
                quality_signals=quality_signals,
            )

    # Should not reach here (found=False already returned above)
    return HydrationExtractionResult(
        found=False,
        sufficient=False,
        sources=(),
        text="",
        metadata={},
        reason=_REASON_NONE,
        hydration_score=0.0,
        quality_signals=(),
    )
