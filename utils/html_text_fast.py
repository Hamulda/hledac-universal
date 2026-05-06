"""
html_text_fast — selectolax-first HTML → text extraction.

Bounded, fail-soft, no network, no browser, no global side effects.

Selectolax is ~10-50x faster than BeautifulSoup for HTML parsing.
Fallback chain: selectolax → regex-stripped html.parser → pure regex.
"""

from __future__ import annotations

import html as _html
import re
from typing import Optional

__all__ = ["html_to_text_fast"]

# Module-level guard — selectolax is optional
try:
    from selectolax.parser import HTMLParser as _SelectolaxParser

    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False
    _SelectolaxParser = None  # type: ignore[assignment]

# Patterns for regex fallback (shared with other modules, defined once)
_RE_SCRIPT_STYLE = re.compile(
    r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|"
    r"<noscript[^>]*>.*?</noscript>",
    re.DOTALL | re.IGNORECASE,
)
# Note: template/svg/canvas need separate removal because nested HTML breaks
# the non-greedy .*? pattern in _RE_SCRIPT_STYLE.
_RE_TEMPLATE = re.compile(r"<template[^>]*>.*?</template>", re.DOTALL | re.IGNORECASE)
_RE_SVG = re.compile(r"<svg[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)
_RE_CANVAS = re.compile(r"<canvas[^>]*>.*?</canvas>", re.DOTALL | re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


def _decode_entities(text: str) -> str:
    """Decode common HTML entities safely (no external dependencies)."""
    # Use stdlib html.unescape for full entity support
    try:
        return _html.unescape(text)
    except Exception:
        return text


def _selectolax_extract(html: str, *, max_chars: Optional[int] = None) -> str:
    """
    Extract text via selectolax (Rust parser, ~10-50x faster than BS4).

    Removes: script, style, noscript, svg, canvas, template elements.
    Normalizes: whitespace, entity decoding.
    """
    if not html:
        return ""

    try:
        tree = _SelectolaxParser(html)  # type: ignore[operator]
    except Exception:
        return ""  # Fail-soft: return empty on parse failure

    # Remove noise elements
    for tag in ("script", "style", "noscript", "svg", "canvas", "template"):
        for node in tree.css(tag):
            node.detach()

    # Extract text from body (or root if no body)
    body = tree.css_first("body")
    if body is None:
        body = tree  # type: ignore[assignment]

    text = body.text(separator=" ")
    text = _decode_entities(text)
    text = _RE_WS.sub(" ", text).strip()

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]

    return text


def _regex_fallback_extract(
    html: str, *, max_chars: Optional[int] = None
) -> str:
    """
    Pure-regex fallback when neither selectolax nor BeautifulSoup are available.

    Matches the behavior of the legacy regex fallback in content_extractor.py.
    """
    if not html:
        return ""

    # Remove script/style/noscript
    text = _RE_SCRIPT_STYLE.sub("", html)
    # Remove template/svg/canvas (separate patterns needed for nested-HTML-safe removal)
    text = _RE_TEMPLATE.sub("", text)
    text = _RE_SVG.sub("", text)
    text = _RE_CANVAS.sub("", text)

    # Remove all remaining tags
    text = _RE_TAG.sub(" ", text)

    # Decode entities
    text = _decode_entities(text)

    # Normalize whitespace
    text = _RE_WS.sub(" ", text).strip()

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]

    return text


def html_to_text_fast(
    html: str, *, max_chars: Optional[int] = None
) -> str:
    """
    Convert HTML to plain text — selectolax-first, bounded, fail-soft.

    Args:
        html: Raw HTML content.
        max_chars: Maximum characters to return (default: None = unlimited).

    Returns:
        Plain text extracted from HTML.
        Malformed HTML returns "" (never raises).
        Empty input returns "".

    Behavior:
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
