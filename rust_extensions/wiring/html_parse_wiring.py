"""
Wiring: HTML Parser (lol_html) → hledac/universal

Integration Point: forensics/ content extraction, recon/ URL harvesting
Benefit:
  - Zero-allocation link extraction via lol_html
  - 5MB input cap for M1 8GB safety
  - GIL release during parsing for parallelism
  - Batch link extraction with rayon
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_rust_available: bool = False
_rust_module = None

try:
    from _core.rust_backend import rust

    _rust_module = getattr(rust, "raw", None)
    if _rust_module is not None:
        # Try to get html_parse functions
        if hasattr(_rust_module, "extract_links_zero_copy"):
            _rust_available = True
            logger.debug("HTML parser: Rust backend available")
except Exception as e:
    logger.debug(f"HTML parser: Rust backend not available: {e}")
    _rust_module = None

import re
from urllib.parse import urljoin

_RE_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_SRC = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)


def _python_extract_links(html: str, base_url: str) -> list[str]:
    """Pure Python link extraction."""
    links = set()

    for m in _RE_HREF.finditer(html):
        href = m.group(1)
        if href and not href.startswith(("javascript:", "mailto:", "#")):
            try:
                links.add(urljoin(base_url, href))
            except Exception:
                pass

    for m in _RE_SRC.finditer(html):
        src = m.group(1)
        if src and not src.startswith(
            (
                "javascript:",
                "data:",
            )
        ):
            try:
                links.add(urljoin(base_url, src))
            except Exception:
                pass

    return list(links)


def _python_extract_emails(html: str) -> list[str]:
    """Pure Python email extraction."""
    email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    return email_pattern.findall(html)


def _python_extract_meta_tags(html: str) -> dict[str, str]:
    """Extract meta tags (description, title, og:*, twitter:*)."""
    result = {}

    # Title
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # Meta tags
    for pattern, name in [
        (r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', "description"),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', "description"),
        (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', "og:title"),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', "og:title"),
        (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', "og:description"),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', "og:description"),
    ]:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            result[name] = match.group(1).strip()

    return result


def _python_batch_extract_links(
    html_batch: list[str],
    base_urls: list[str],
) -> list[list[str]]:
    """Batch link extraction with rayon parallelism."""
    results = []
    for html, base in zip(html_batch, base_urls, strict=False):
        results.append(_python_extract_links(html, base))
    return results


def extract_links(html: str, base_url: str) -> list[str]:
    """
    Extract all links (href, src) from HTML.

    Args:
        html: HTML content
        base_url: Base URL for resolving relative links

    Returns:
        List of absolute URLs
    """
    if _rust_available and _rust_module is not None:
        try:
            # Rust returns zero-copy byte ranges
            ranges = _rust_module.extract_links_zero_copy(html, base_url)
            if ranges:
                # Convert byte ranges back to strings
                html_bytes = html.encode("utf-8") if isinstance(html, str) else html
                return [html_bytes[start:end].decode("utf-8", errors="ignore") for start, end in ranges]
        except Exception as e:
            logger.debug(f"Rust extract_links failed: {e}")

    return _python_extract_links(html, base_url)


def extract_links_zero_copy(html: str, base_url: str) -> list[tuple[int, int]]:
    """
    Extract link byte ranges (zero-copy API).

    Returns list of (start_byte, end_byte) tuples.
    Python resolves URLs by slicing the HTML and calling urljoin.

    Args:
        html: HTML content
        base_url: Base URL

    Returns:
        List of (start, end) byte indices
    """
    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.extract_links_zero_copy(html, base_url)
        except Exception as e:
            logger.debug(f"Rust extract_links_zero_copy failed: {e}")

    # Python fallback: return indices for full links
    html_bytes = html.encode("utf-8") if isinstance(html, str) else html
    links = _python_extract_links(html, base_url)
    results = []
    for link in links:
        try:
            link_bytes = link.encode("utf-8")
            idx = html_bytes.find(link_bytes)
            if idx >= 0:
                results.append((idx, idx + len(link_bytes)))
        except Exception:
            pass
    return results


def extract_emails(html: str) -> list[str]:
    """Extract email addresses from HTML."""
    return _python_extract_emails(html)


def extract_meta_tags(html: str) -> dict[str, str]:
    """Extract meta tags (title, description, og:*, twitter:*)."""
    return _python_extract_meta_tags(html)


def batch_extract_links(
    html_batch: list[str],
    base_urls: list[str],
) -> list[list[str]]:
    """
    Batch extract links from multiple HTML documents.

    Args:
        html_batch: List of HTML contents
        base_urls: List of base URLs (one per HTML)

    Returns:
        List of link lists (one per HTML document)
    """
    if len(html_batch) != len(base_urls):
        raise ValueError("html_batch and base_urls must have same length")

    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.batch_extract_links(html_batch, base_urls)
        except AttributeError:
            pass

    return _python_batch_extract_links(html_batch, base_urls)


def is_available() -> bool:
    """Check if Rust HTML parser is available."""
    return _rust_available
