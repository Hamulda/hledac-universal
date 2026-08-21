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

import orjson

from compat.msgspec_gc_compat import Struct

MAX_HTML_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MB input cap
MAX_EXTRACTED_TEXT: Final[int] = 100_000  # 100 KB output cap
MAX_JSON_LD_BLOCKS: Final[int] = 10  # max JSON-LD script blocks
MAX_JSON_DEPTH: Final[int] = 20  # max traversal depth
MAX_SCRIPT_LEN: Final[int] = 500_000  # 500 KB per script block
MAX_TITLE_LEN: Final[int] = 500  # max title chars
MAX_METADATA_LEN: Final[int] = 2000  # max metadata dict serialized
MAX_CANDIDATE_LEN: Final[int] = 50_000  # max single JSON candidate text

_REASON_SUFFICIENT_NEXT = "next_data_sufficient"
_REASON_SUFFICIENT_NUXT = "nuxt_data_sufficient"
_REASON_SUFFICIENT_JSON_LD = "json_ld_sufficient"
_REASON_SUFFICIENT_METADATA = "metadata_sufficient"
_REASON_FOUND_INSUFFICIENT = "hydration_found_but_insufficient"
_REASON_NONE = "no_hydration_found"
# F265C: body-content regexes for HTML-level content depth check
_RE_BODY_TAGS: re.Pattern = re.compile(
    r"<(?:p|article|main|section|div[^>]*|ul|ol|dl|table|blockquote|h[2-6])[^>]*>",
    re.IGNORECASE,
)
_RE_SKIP_TAGS: re.Pattern = re.compile(
    r"<script[^>]*>|<style[^>]*>|<noscript[^>]*>|<svg[^>]*>|<canvas[^>]*>",
    re.IGNORECASE,
)
# Reserved for future telemetry (not currently emitted by extract_static_hydration):
# _REASON_PARSE_ERROR = "parse_error"
# _REASON_MAX_BYTES = "max_bytes_exceeded"


class HydrationExtractionResult(Struct):
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


# Next.js __NEXT_DATA__
_RE_NEXT_DATA: re.Pattern[str] = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Nuxt __NUXT_DATA__ (SSR rendered)
_RE_NUXT_DATA: re.Pattern[str] = re.compile(
    r"<script[^>]*>(?:window\.)?__NUXT_DATA__\s*=\s*(\[.*?\]);?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)

# Nuxt window.__NUXT__
_RE_NUXT_GLOBAL: re.Pattern[str] = re.compile(
    r"<script[^>]*>window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)

# Generic hydration
_RE_INITIAL_STATE: re.Pattern[str] = re.compile(
    r"<script[^>]*>(?:window\.)?__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)
_RE_PRELOADED_STATE: re.Pattern[str] = re.compile(
    r"<script[^>]*>(?:window\.)?__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)
_RE_APOLLO_STATE: re.Pattern[str] = re.compile(
    r"<script[^>]*>(?:window\.)?__APOLLO_STATE__\s*=\s*(\{.*?\});?\s*</script>",
    re.DOTALL | re.IGNORECASE,
)

# JSON-LD
_RE_JSON_LD: re.Pattern[str] = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Metadata
_RE_CANONICAL: re.Pattern[str] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*canonical[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_RSS: re.Pattern[str] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*alternate[^"\']*["\'][^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_ATOM: re.Pattern[str] = re.compile(
    r'<link[^>]+rel=["\'][^"\']*alternate[^"\']*["\'][^>]+type=["\']application/atom\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_TITLE: re.Pattern[str] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_DESC: re.Pattern[str] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_META_DESC: re.Pattern[str] = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_TITLE_TAG: re.Pattern[str] = re.compile(
    r"<title[^>]*>(.*?)</title>",
    re.DOTALL | re.IGNORECASE,
)
_RE_OG_IMAGE: re.Pattern[str] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_OG_URL: re.Pattern[str] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_ARTICLE_PUBLISHED: re.Pattern[str] = re.compile(
    r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Article",
        "NewsArticle",
        "BlogPosting",
        "Person",
        "Organization",
        "WebSite",
        "BreadcrumbList",
        "Product",
        "Event",
    }
)

# Minimum lengths for sufficiency heuristic
_MIN_TITLE_LEN: Final[int] = 15
_MIN_BODY_LEN: Final[int] = 50


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


# Content fields for JSON extraction (prioritized)
_CONTENT_FIELDS: tuple[str, ...] = (
    "props",
    "pageProps",
    "serverData",
    "data",
    "body",
    "content",
    "text",
    "html",
    "result",
    "articleBody",
    "description",
    "headline",
)


def _is_valid_text(text: str) -> bool:
    """Check if text is valid for extraction."""
    return 2 < len(text) < MAX_CANDIDATE_LEN


def _extract_str(obj) -> str:
    """Extract text from a string value."""
    text = obj.strip()
    return text if _is_valid_text(text) else ""


def _extract_list(obj, depth: int, seen: set[int]) -> str:
    """Extract text from a list by recursing into items."""
    parts = []
    for item in obj:
        text = _flatten_text(item, depth + 1, seen)
        if text:
            parts.append(text)
        if sum(len(p) for p in parts) > MAX_EXTRACTED_TEXT:
            break
    return " ".join(parts)


def _extract_dict(obj, depth: int, seen: set[int]) -> str:
    """Extract text from a dict by checking prioritized content fields."""
    parts = []
    for key in _CONTENT_FIELDS:
        if key in obj:
            text = _flatten_text(obj[key], depth + 1, seen)
            if text:
                parts.append(text)

    # Fallback: scan all values if no content fields found
    if not parts:
        for v in obj.values():
            if isinstance(v, str):
                text = v.strip()
                if _is_valid_text(text):
                    parts.append(text)
            elif isinstance(v, dict):
                text = _flatten_text(v, depth + 1, seen)
                if text:
                    parts.append(text)

    return " ".join(parts)


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
        return _extract_str(obj)
    if isinstance(obj, list):
        return _extract_list(obj, depth, seen)
    if isinstance(obj, dict):
        return _extract_dict(obj, depth, seen)
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
        info.get("body", "") or info.get("description", "") or info.get("json_ld_text", "") or info.get("meta_desc", "")
    )
    return bool(body and len(body) >= _MIN_BODY_LEN)


def _has_content_json_ld(info: dict) -> bool:
    """Check if info has JSON-LD type from CONTENT_TYPES."""
    return bool(info.get("json_ld_types") and any(t in _CONTENT_TYPES for t in info.get("json_ld_types", [])))


def _has_metadata_signal(info: dict) -> bool:
    """Check if info has canonical/feed/alternate links."""
    metadata = info.get("metadata", {})
    return bool(metadata.get("canonical") or metadata.get("rss") or metadata.get("atom"))


def _has_body_content_html(html: str) -> bool:
    """
    F265C: Check if raw HTML contains actual body content elements.

    This is the content-depth check that prevents metadata-only pages
    (OpenSearch JSON, JSON-LD without article body, etc.) from being
    marked as sufficient. Pages with only <meta> tags but no <p>,
    <article>, <main>, <section>, <ul>, <ol>, <dl>, <table>, <blockquote>,
    or heading tags are NOT sufficient — they need JS rendering.

    Returns True if at least one body-content tag is found after
    stripping skip tags (script/style/noscript/svg/canvas).
    """
    if not html or len(html) < 100:
        return False
    # Remove skip tags to avoid false positives from template code
    stripped = _RE_SKIP_TAGS.sub("", html)
    return bool(_RE_BODY_TAGS.search(stripped))


def _get_json_ld_type_signals(json_ld_types: list[str]) -> list[str]:
    """Extract JSON-LD type signals from types list."""
    return [f"json_ld_{t.lower()}" for t in json_ld_types if t in _CONTENT_TYPES]


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
    metadata = info.get("metadata", {})

    # Pre-compute common values
    total_text = info.get("body", "") or info.get("description", "") or info.get("json_ld_text", "")
    is_short_text = bool(total_text) and len(total_text) < 100

    # Additive scoring: title (+0.2), body (+0.3), json_ld (+0.3)
    if _has_meaningful_title(info):
        score += 0.2
        signals.append("title")
    if _has_meaningful_body(info):
        score += 0.3
        signals.append("body")
    if _has_content_json_ld(info):
        score += 0.3
        signals.append("json_ld_article")
        signals.extend(_get_json_ld_type_signals(info.get("json_ld_types", [])))

    # Metadata: canonical (+0.1), feeds (+0.1)
    if metadata.get("canonical"):
        score += 0.1
        signals.append("canonical")
    if metadata.get("rss") or metadata.get("atom"):
        score += 0.1
        signals.append("feed_alternate")

    # Hydration payload (+0.4)
    body_source = info.get("_body_source", "")
    if body_source in (_REASON_SUFFICIENT_NEXT, _REASON_SUFFICIENT_NUXT, _REASON_SUFFICIENT_METADATA) and info.get(
        "body"
    ):
        score += 0.4
        signals.append("hydration_payload")

    # Penalties
    if input_truncated:
        score -= 0.2
        signals.append("input_truncated")
    if is_short_text:
        score -= 0.1
        signals.append("short_text")

    return (max(0.0, min(1.0, score)), tuple(signals))


def _is_sufficient(info: dict, html: str = "") -> tuple[bool, str]:
    """
    Conservative sufficiency check.
    Returns (sufficient, reason_str).

    F265C: body-content depth check — pages with only metadata (title + canonical/feed)
    but no actual body content elements in HTML are NOT sufficient. They need JS
    rendering to extract real article content. Pass raw html for the depth check.
    """
    has_title = _has_meaningful_title(info)
    has_body = _has_meaningful_body(info)
    has_json_ld_content = _has_content_json_ld(info)
    has_meta_signal = _has_metadata_signal(info)

    if has_title and (has_body or has_meta_signal):
        if has_json_ld_content:
            return True, _REASON_SUFFICIENT_JSON_LD
        if has_body:
            # Title + body from info dict is sufficient
            return True, info.get("_body_source", _REASON_SUFFICIENT_METADATA)
        # F265C: has_meta_signal=True but has_body=False — title + meta only
        # Require actual body content in HTML or this is a metadata-only page
        if _has_body_content_html(html):
            return True, _REASON_SUFFICIENT_METADATA
        return False, ""
    if has_json_ld_content and (has_title or has_meta_signal):
        return True, _REASON_SUFFICIENT_JSON_LD
    if has_title and has_meta_signal:
        # F265C: title + canonical/feed is NOT sufficient without body content
        # Check raw HTML for body elements — metadata-only pages need JS rendering
        if _has_body_content_html(html):
            return True, _REASON_SUFFICIENT_METADATA
        return False, ""
    return False, ""


# Title extraction dispatch table: (name, path/key extractor, validator)
# Single-pass consolidates Next.js, Nuxt, and generic patterns
type _TitleHandlers = tuple[
    tuple[str, tuple[str, ...], callable],  # name, path tuple, validator
    ...,
]

_TITLE_HANDLERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Next.js patterns (path-based)
    (("props", "pageProps", "title"), ("props", "pageProps", "title")),
    (("props", "pageProps", "serverData", "title"), ("props", "pageProps", "serverData", "title")),
    (("props", "pageProps", "data", "title"), ("props", "pageProps", "data", "title")),
    (("pageProps", "title"), ("pageProps", "title")),
    (("title",), ("title",)),
    # Nuxt specific (index 0)
    (("__nuxt_index_0",), ("__nuxt_index_0",)),
    # Generic fallback keys
    (("serverData", "title"), ("serverData", "title")),
    (("data", "title"), ("data", "title")),
    (("ROOT_QUERY", "title"), ("ROOT_QUERY", "title")),
)


def _extract_title_from_parsed(parsed) -> str:
    """Extract title using single-pass dispatch table (Next.js, Nuxt, generic)."""
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict):
            title = first.get("data", {}).get("title") or first.get("title", "")
            if title and len(str(title)) >= _MIN_TITLE_LEN:
                return str(title)

    for path, _ in _TITLE_HANDLERS:
        val = parsed
        for key in path:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                break
        if val and len(str(val)) >= _MIN_TITLE_LEN:
            return str(val)

    return ""


# Type alias for hydration handler entries (source_name, regex, title_extractor, reason)
type _HydrationHandler = tuple[str, re.Pattern[str], callable[[object], str], str]


def _try_hydration_pattern(
    html: str,
    pattern: re.Pattern[str],
    title_extractor: callable[[object], str],
) -> dict | None:
    """
    Try to extract and parse a hydration pattern from HTML.

    Returns parsed JSON dict on success, None on failure.
    Uses short-circuit evaluation for fail-fast behavior.
    """
    if raw := _extract_from_script(html, pattern):
        return _safe_json_parse(raw)
    return None


def _extract_json_hydration(html: str, info: dict, sources: list) -> None:
    """
    Extract JSON-based hydration using single-pass dispatch table.

    Single dispatch table with priority ordering:
    1. Framework-specific: Next.js, Nuxt (highest priority)
    2. Generic: Initial/Preloaded/Apollo state (fallback)

    Uses early-exit pattern when body already extracted.
    """
    # Single unified dispatch table: (source_name, regex, reason)
    hydration_handlers: tuple[tuple[str, re.Pattern[str], str], ...] = (
        # Framework-specific (priority order)
        ("next_data", _RE_NEXT_DATA, _REASON_SUFFICIENT_NEXT),
        ("nuxt_data", _RE_NUXT_DATA, _REASON_SUFFICIENT_NUXT),
        ("nuxt_data", _RE_NUXT_GLOBAL, _REASON_SUFFICIENT_NUXT),
        # Generic fallback
        ("initial_state", _RE_INITIAL_STATE, _REASON_SUFFICIENT_METADATA),
        ("preloaded_state", _RE_PRELOADED_STATE, _REASON_SUFFICIENT_METADATA),
        ("apollo_state", _RE_APOLLO_STATE, _REASON_SUFFICIENT_METADATA),
    )

    # Single pass: try all patterns, early-exit on body found
    for name, pattern, reason in hydration_handlers:
        if info.get("body") and sources:
            break  # Early exit: body content already extracted
        if parsed := _try_hydration_pattern(html, pattern, _extract_title_from_parsed):
            sources.append(name)
            if text := _flatten_text(parsed):
                info["body"] = _truncate(text, MAX_EXTRACTED_TEXT)
            if title := _extract_title_from_parsed(parsed):
                info["title"] = _truncate(str(title), MAX_TITLE_LEN)
            info["_body_source"] = reason


def _extract_title_from_json_ld(parsed_blocks: list) -> str:
    """Extract title from first parsed JSON-LD block."""
    for parsed in parsed_blocks:
        if headline := parsed.get("headline") or parsed.get("name"):
            if len(headline) >= _MIN_TITLE_LEN:
                return _truncate(headline, MAX_TITLE_LEN)
    return ""


def _extract_json_ld(html: str, info: dict, sources: list) -> None:
    """Extract JSON-LD blocks from HTML (single-pass optimization)."""
    json_ld_types: list[str] = []
    json_ld_texts: list[str] = []
    parsed_blocks: list = []

    # Single pass: collect types, texts, and parsed blocks
    for match in _RE_JSON_LD.finditer(html):
        if len(parsed_blocks) >= MAX_JSON_LD_BLOCKS:
            break
        if len(match.group(1)) > MAX_SCRIPT_LEN:
            continue
        if parsed := _safe_json_parse(match.group(1).strip()):
            parsed_blocks.append(parsed)
            _json_ld_types(parsed, json_ld_types)
            if text := _flatten_text(parsed):
                json_ld_texts.append(text)

    # Early exit if no types found
    if not json_ld_types:
        return

    # Populate info
    sources.append("json_ld")
    info["json_ld_types"] = json_ld_types
    info["json_ld_text"] = _truncate(" ".join(json_ld_texts), MAX_EXTRACTED_TEXT)

    # Body assignment (only if not already set)
    if not info.get("body") and json_ld_texts:
        info["body"] = info["json_ld_text"]

    # Title extraction from JSON-LD
    if not info.get("title"):
        if title := _extract_title_from_json_ld(parsed_blocks):
            info["title"] = title


def _extract_metadata(html: str, info: dict) -> dict:
    """Extract metadata tags using dictionary dispatch table (Python 3.14+ pattern)."""
    # Dispatch table: (regex_pattern, key_name)
    metadata_patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (_RE_CANONICAL, "canonical"),
        (_RE_RSS, "rss"),
        (_RE_ATOM, "atom"),
        (_RE_OG_TITLE, "og_title"),
        (_RE_OG_DESC, "og_description"),
        (_RE_META_DESC, "meta_description"),
        (_RE_TITLE_TAG, "title_tag"),
        (_RE_OG_IMAGE, "og_image"),
        (_RE_OG_URL, "og_url"),
        (_RE_ARTICLE_PUBLISHED, "article_published_time"),
    )

    metadata: dict[str, object] = {}
    for pattern, key in metadata_patterns:
        if m := pattern.search(html):
            metadata[key] = m.group(1).strip()

    # Update info with derived values (walrus operator)
    if not info.get("title"):
        info["title"] = metadata.get("og_title", "") or metadata.get("title_tag", "") or ""
    if not info.get("description"):
        info["description"] = metadata.get("og_description", "") or metadata.get("meta_description", "") or ""
    if not info.get("meta_desc"):
        info["meta_desc"] = metadata.get("meta_description", "") or ""

    return metadata


def _build_hydration_result(
    info: dict,
    sources: list,
    metadata: dict,
    hydration_score: float,
    quality_signals: tuple,
    input_truncated: bool,
    html: str,
) -> HydrationExtractionResult:
    """Build final HydrationExtractionResult from extracted info (DRY pattern)."""
    # Fast path: nothing found
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

    # Sufficiency check
    sufficient, reason = _is_sufficient(info, html)

    # Build composite text only when sufficient
    final_text = ""
    if sufficient:
        parts: list[str] = []
        if title := info.get("title"):
            parts.append(title)
        if body := info.get("body") or info.get("description", ""):
            parts.append(body)
        final_text = _truncate(" | ".join(parts), MAX_EXTRACTED_TEXT)

    return HydrationExtractionResult(
        found=True,
        sufficient=sufficient,
        sources=tuple(sources),
        text=final_text,
        metadata=metadata,
        reason=reason if sufficient else _REASON_FOUND_INSUFFICIENT,
        hydration_score=hydration_score,
        quality_signals=quality_signals,
    )


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

    # Bounds: truncate oversized input (M1 8GB safe: single pass)
    input_truncated = len(html) > max_bytes
    if input_truncated:
        html = html[:max_bytes]

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

    _extract_json_hydration(html, info, sources)

    _extract_json_ld(html, info, sources)

    metadata = _extract_metadata(html, info)

    hydration_score, quality_signals = _compute_hydration_score(info, input_truncated)
    return _build_hydration_result(
        info=info,
        sources=sources,
        metadata=metadata,
        hydration_score=hydration_score,
        quality_signals=quality_signals,
        input_truncated=input_truncated,
        html=html,
    )
