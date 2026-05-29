"""
html_text_fast — selectolax-first HTML → text extraction + metadata extraction.

Bounded, fail-soft, no network, no browser, no global side effects.

Metadata extraction (F229):
- Google Analytics/Tag Manager IDs (UA-*, GTM-*)
- Open Graph meta tags (og:*) — max 5
- HTML comments — first 500 chars each, max unbounded

Invariant: extract_html_metadata() runs BEFORE selectolax text extraction
so metadata can be collected even when text parsing fails.
"""

from __future__ import annotations

import html as _html
import re
from typing import Optional

try:
    from selectolax.parser import HTMLParser as _SelectolaxHTMLParser
    from selectolax.tags import Node as _SelectolaxNode

    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False

    class _SelectolaxNode:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Patterns for regex fallback (shared with other modules, defined once)
# ---------------------------------------------------------------------------
_RE_SCRIPT_STYLE = re.compile(
    r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|"
    r"<noscript[^>]*>.*?</noscript>",
    re.DOTALL | re.IGNORECASE,
)
_RE_TEMPLATE = re.compile(r"<template[^>]*>.*?</template>", re.DOTALL | re.IGNORECASE)
_RE_SVG = re.compile(r"<svg[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)
_RE_CANVAS = re.compile(r"<canvas[^>]*>.*?</canvas>", re.DOTALL | re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# F229: HTML metadata extraction — runs BEFORE text extraction
# ---------------------------------------------------------------------------

# Google Analytics / Tag Manager
_RE_GA_ID = re.compile(r"UA-\d{6,10}-\d{1,4}|GTM-[A-Z0-9]{1,8}", re.IGNORECASE)

# Open Graph — og:property names (og:title, og:description, og:image, etc.)
_RE_OG_TAG = re.compile(
    r'<meta\s+(?:property|content)=["\']og:([a-zA-Z0-9_:-]+)["\']\s+(?:content|property)=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
# Also handle reversed attribute order (content before property)
_RE_OG_TAG_REV = re.compile(
    r'<meta\s+content=["\']([^"\']*)["\']\s+property=["\']og:([a-zA-Z0-9_:-]+)["\']',
    re.IGNORECASE,
)

# HTML comments
_RE_COMMENT = re.compile(r"<!--[\s\S]*?-->")


def extract_html_metadata(html: str) -> dict:
    """
    Extract Google Analytics/Tag Manager IDs, OG meta tags, and HTML comments
    from raw HTML.

    Memory bounds:
    - OG tags: max 5 entries (first 5 unique og:* properties found)
    - Comment chars: first 500 chars per comment

    Returns dict with keys:
    - ga_gtm_ids: tuple[str, ...] — unique GA/GTM IDs found
    - og_tags: tuple[tuple[str, str], ...] — (property, content) pairs, max 5
    - comments: tuple[str, ...] — comment bodies, truncated to 500 chars each
    """
    if not html or not isinstance(html, str):
        return {"ga_gtm_ids": (), "og_tags": (), "comments": ()}

    ga_ids: set[str] = set()
    og_tags: list[tuple[str, str]] = []
    og_props_seen: set[str] = set()
    comments: list[str] = []

    # --- GA/GTM IDs ---
    for _mid in _RE_GA_ID.findall(html):
        if len(ga_ids) >= 20:  # hard cap on total IDs
            break
        ga_ids.add(_mid)

    # --- OG tags (max 5 unique properties) ---
    for _m in _RE_OG_TAG.finditer(html):
        _prop, _content = _m.group(1).strip(), _m.group(2).strip()
        if _prop not in og_props_seen and len(og_tags) < 5:
            og_tags.append((_prop, _content[:500]))  # cap content per tag
            og_props_seen.add(_prop)
    if len(og_tags) < 5:
        for _m in _RE_OG_TAG_REV.finditer(html):
            _content, _prop = _m.group(1).strip(), _m.group(2).strip()
            if _prop not in og_props_seen and len(og_tags) < 5:
                og_tags.append((_prop, _content[:500]))
                og_props_seen.add(_prop)

    # --- HTML comments (first 500 chars each) ---
    for _cm in _RE_COMMENT.finditer(html):
        _body = _cm.group(0)[4:-3].strip()  # strip <!-- and -->
        if _body:
            comments.append(_body[:500])
            if len(comments) >= 50:  # hard cap on total comments
                break

    return {
        "ga_gtm_ids": tuple(sorted(ga_ids)),
        "og_tags": tuple(og_tags),
        "comments": tuple(comments),
    }


# ---------------------------------------------------------------------------
# Text extraction — shared helpers
# ---------------------------------------------------------------------------


def _decode_entities(text: str) -> str:
    """Decode common HTML entities safely (no external deps)."""
    try:
        return _html.unescape(text)
    except Exception:
        return text


# ---------------------------------------------------------------------------
# selectolax path
# ---------------------------------------------------------------------------


def _selectolax_extract(html: str, *, max_chars: int | None = None) -> str:
    """selectolax-based extraction (fastest, Rust parser)."""
    try:
        tree = _SelectolaxHTMLParser(html)
    except Exception:
        return ""

    # Remove noise tags that add no text value
    for tag in tree.css("script, style, noscript, template, svg, canvas"):
        tag.decompose()

    body = tree.body
    if body is None:
        return ""

    text = body.text(separator=" ")
    text = _decode_entities(text)
    text = _RE_WS.sub(" ", text).strip()

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]

    return text


# ---------------------------------------------------------------------------
# Pure-regex fallback path
# ---------------------------------------------------------------------------


def _regex_fallback_extract(
    html: str, *, max_chars: int | None = None
) -> str:
    """
    Pure-regex fallback when neither selectolax nor BeautifulSoup are available.
    Strips script/style/noscript/template/svg/canvas, then extracts visible text.
    """
    if not html:
        return ""

    text = html

    # Remove CDATA-like blocks first
    text = re.sub(r"<!\[CDATA\[[\s\S]*?\]\]>", "", text)

    for _pat in (_RE_SCRIPT_STYLE, _RE_TEMPLATE, _RE_SVG, _RE_CANVAS):
        text = _pat.sub("", text)

    text = _RE_TAG.sub(" ", text)
    text = _decode_entities(text)
    text = _RE_WS.sub(" ", text).strip()

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]

    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

opt: type = Optional


def html_to_text_fast(
    html: str,
    *,
    max_chars: int | None = None,
) -> str:
    """
    Convert HTML to plain text using the best available parser.

    Priority:
        1. selectolax (fastest, Rust parser) — if available
        2. regex fallback (no external deps) — if selectolax unavailable

    Removed elements (always):
        script, style, noscript, svg, canvas, template

    Normalized:
        - HTML entities decoded
        - Whitespace collapsed to single spaces
    """
    if not html:
        return ""

    # Fast path: selectolax
    if SELECTOLAX_AVAILABLE:
        try:
            return _selectolax_extract(html, max_chars=max_chars)
        except Exception:
            # selectolax failed mid-parse — fall through to regex
            pass

    # Fallback: pure regex (no external deps)
    return _regex_fallback_extract(html, max_chars=max_chars)
