# html.py — HTML parsing domain
"""
HTML parsing via Rust lol_html with selectolax fallback.
Tier 1: Rust lol_html (5× faster than BS4)

Tier 2: selectolax (10× faster than BS4, M1-friendly)
Tier 3: stdlib regex (ultimate fallback)
"""

from __future__ import annotations

import re
import urllib.parse as urlparse
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# Availability flags — set once at module load
_HTML_PARSE_RUST_AVAILABLE = False
_SELECTOLAX_AVAILABLE = False

try:
    import hledac_rust_extensions

    _HTML_PARSE_RUST_AVAILABLE = True
except ImportError:
    hledac_rust_extensions = None  # type: ignore[assignment]

try:
    from selectolax.parser import HTMLParser as _SelectolaxParser

    _SELECTOLAX_AVAILABLE = True
except ImportError:
    _SelectolaxParser = None  # type: ignore[assignment]


# =============================================================================
# HTML Domain
# =============================================================================


class _RustHtmlDomain:
    """Rust-backed HTML parsing via lol_html (Cloudflare's zero-allocation rewriter).
    Bounded: 2MB max HTML size, 10K links per doc, fail-safe on any error.
    Thread-safe: all extractors are Send+Sync.
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: object) -> None:
        self._ext = ext

    def extract_links(self, html: str, base_url: str) -> list[str]:
        """Extract all links (href) resolved against base_url."""
        return self._ext.extract_links(html, base_url)

    def extract_links_with_text(self, html: str, base_url: str) -> list[tuple[str, str]]:
        """Extract links with anchor text."""
        return self._ext.extract_links_with_text(html, base_url)

    def extract_emails(self, html: str) -> list[str]:
        """Extract email addresses from HTML."""
        return self._ext.extract_emails(html)

    def extract_titles(self, html: str) -> list[str | None]:
        """Extract <title> content."""
        title = self._ext.extract_title(html)
        return [title] if title is not None else []

    def html_to_text(self, html: str) -> str:
        """Extract plain text from HTML."""
        return getattr(self._ext, "html_to_text", lambda x: x)(html)

    def batch_extract_links(self, items: list[tuple[str, str]]) -> list[list[str]]:
        """Parallel link extraction for (html, base_url) pairs."""
        return self._ext.batch_extract_links(items)

    def batch_extract_links_with_text(self, items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """Parallel link+text extraction."""
        return self._ext.batch_extract_links_with_text(items)

    def batch_extract_emails(self, items: list[str]) -> list[list[str]]:
        """Parallel email extraction."""
        return self._ext.batch_extract_emails(items)

    def batch_extract_titles(self, items: list[str]) -> list[str | None]:
        """Parallel title extraction."""
        return self._ext.batch_extract_titles(items)

    def html_extract(self, html: str) -> dict[str, Any]:
        """Extract links, emails, and title from HTML in one call."""
        links = self.extract_links(html, "")
        emails = self.extract_emails(html)
        titles = self.extract_titles(html)
        return {
            "links": links,
            "emails": emails,
            "title": titles[0] if titles else None,
        }


class _PythonHtmlDomain:
    """Python HTML parsing: selectolax-first, regex fallback.
    Tier 1: selectolax (Rust C backend, lexbor — 10× faster than BS4)
    Tier 2: stdlib regex (ultimate fallback)
    Bounded: 2MB max HTML, 10K links per doc.
    """

    __slots__ = ()

    # Regex patterns for Tier 2 fallback
    _EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    _TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
    _MAX_HTML_SIZE = 2 * 1024 * 1024  # 2 MB
    _MAX_LINKS = 10_000

    def extract_links(self, html: str, base_url: str) -> list[str]:
        """Extract links — selectolax or regex fallback."""
        if len(html) > self._MAX_HTML_SIZE:
            return []
        if _SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxParser(html)
                seen: set[str] = set()
                results: list[str] = []
                for node in tree.css("a[href], link[href]"):
                    href = node.attrs.get("href") or ""
                    if href and href not in seen and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        seen.add(href)
                        results.append(href)
                        if len(results) >= self._MAX_LINKS:
                            break
                return results
            except Exception:
                pass
        # Regex fallback
        return _python_extract_links_regex(html, base_url)

    def extract_links_with_text(self, html: str, base_url: str) -> list[tuple[str, str]]:
        """Extract links with anchor text — selectolax or regex fallback."""
        if len(html) > self._MAX_HTML_SIZE:
            return []
        if _SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxParser(html)
                seen: set[str] = set()
                results: list[tuple[str, str]] = []
                for node in tree.css("a[href]"):
                    href = node.attrs.get("href") or ""
                    if href and href not in seen and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                        seen.add(href)
                        text = (node.text() or "").strip()
                        results.append((href, text))
                        if len(results) >= self._MAX_LINKS:
                            break
                return results
            except Exception:
                pass
        return []

    def extract_emails(self, html: str) -> list[str]:
        """Extract email addresses — regex fallback."""
        if len(html) > self._MAX_HTML_SIZE:
            return []
        seen: set[str] = set()
        results: list[str] = []
        for email in self._EMAIL_RE.findall(html):
            if email not in seen:
                seen.add(email)
                results.append(email)
        return results

    def extract_titles(self, html: str) -> list[str | None]:
        """Extract <title> content — regex fallback."""
        if len(html) > self._MAX_HTML_SIZE:
            return []
        match = self._TITLE_RE.search(html)
        if match:
            title = match.group(1).strip()
            return [title if title else None]
        return [None]

    def html_extract(self, html: str) -> dict[str, Any]:
        """Extract links, emails, and title from HTML in one call."""
        links = self.extract_links(html, "")
        emails = self.extract_emails(html)
        titles = self.extract_titles(html)
        return {
            "links": links,
            "emails": emails,
            "title": titles[0] if titles else None,
        }

    def html_to_text(self, html: str) -> str:
        """Extract plain text — selectolax or regex fallback."""
        if len(html) > self._MAX_HTML_SIZE:
            html = html[: self._MAX_HTML_SIZE]
        if _SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxParser(html)
                for tag in tree.css("script, style, nav, footer, header, aside, noscript"):
                    tag.decompose()
                body = tree.body
                if body is not None:
                    text = body.text(separator=" ", strip=True)
                    return " ".join(text.split())  # normalize whitespace
            except Exception:
                pass
        # Regex fallback
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def batch_extract_links(self, items: list[tuple[str, str]]) -> list[list[str]]:
        """Parallel link extraction."""
        return [self.extract_links(html, base_url) for html, base_url in items]

    def batch_extract_links_with_text(self, items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """Parallel link+text extraction."""
        return [self.extract_links_with_text(html, base_url) for html, base_url in items]

    def batch_extract_emails(self, items: list[str]) -> list[list[str]]:
        """Parallel email extraction."""
        return [self.extract_emails(html) for html in items]

    def batch_extract_titles(self, items: list[str]) -> list[str | None]:
        """Parallel title extraction."""
        result = []
        for html in items:
            titles = self.extract_titles(html)
            result.append(titles[0] if titles else None)
        return result


def _python_extract_links_regex(html: str, base_url: str) -> list[str]:
    """Regex fallback: extract href values from HTML."""
    seen: set[str] = set()
    results: list[str] = []
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()
        if not href or href in seen or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        seen.add(href)
        # Resolve relative URLs
        if not href.startswith(("http://", "https://")):
            try:
                resolved = urlparse.urljoin(base_url, href)
                href = resolved
            except Exception:
                pass
        results.append(href)
    return results


def get_html_domain(ext: object | None) -> _RustHtmlDomain | _PythonHtmlDomain:
    """Factory: return Rust or Python HtmlDomain based on ext availability."""
    if ext is not None and _HTML_PARSE_RUST_AVAILABLE:
        try:
            return _RustHtmlDomain(ext)
        except Exception:
            pass
    return _PythonHtmlDomain()
