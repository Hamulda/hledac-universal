"""
Content extractor module - import-safe with bounded extraction.
Extracts main text from HTML and structured data from previews.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# BeautifulSoup is optional - use fallback if not available
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None

# F214OPT-A: selectolax-first HTML→text
try:
    from hledac.universal.utils.html_text_fast import html_to_text_fast

    HTML_TEXT_FAST_AVAILABLE = True
except ImportError:
    HTML_TEXT_FAST_AVAILABLE = False
    html_to_text_fast = None  # type: ignore[assignment]

# Tier 2 migration: selectolax-first for title + links extraction
try:
    from selectolax.parser import HTMLParser as SelectoLAXParser

    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False
    SelectoLAXParser = None  # type: ignore[assignment]


def extract_main_text_from_html(html_preview: str, max_chars: int = 20_000) -> str:
    """
    Extract main text content from HTML preview.

    F214OPT-A: uses html_to_text_fast (selectolax-first) when available,
    preserving the same extraction semantics as the previous BeautifulSoup path.

    Args:
        html_preview: HTML content (first 50KB recommended)
        max_chars: Maximum characters to return

    Returns:
        Extracted text content, bounded
    """
    if not html_preview:
        return ""

    # Truncate to avoid huge processing
    html_preview = html_preview[:50_000]

    # F214OPT-A: selectolax-first (fastest)
    if HTML_TEXT_FAST_AVAILABLE:
        try:
            return html_to_text_fast(html_preview, max_chars=max_chars)  # type: ignore[operator]
        except Exception as e:
            logger.warning("html_to_text_fast failed: %s", e)
            # fall through to BeautifulSoup/regex fallback

    # BeautifulSoup path (legacy)
    try:
        if BS4_AVAILABLE:
            soup = BeautifulSoup(html_preview, 'html.parser')  # type: ignore[operator]

            # Remove script and style elements
            for tag in soup(['script', 'style', 'noscript']):
                tag.decompose()

            # Try common content containers
            main_content = ""
            for selector in ['main', 'article', '[role="main"]', '.content', '.post-content', '.entry-content', '#content']:
                content_elem = soup.select_one(selector)
                if content_elem:
                    main_content = content_elem.get_text(separator=' ', strip=True)
                    break

            # Fallback to body
            if not main_content:
                body = soup.find('body')
                if body:
                    main_content = body.get_text(separator=' ', strip=True)
                else:
                    main_content = soup.get_text(separator=' ', strip=True)

            # Clean whitespace
            main_content = re.sub(r'\s+', ' ', main_content).strip()
        else:
            # Fallback: simple regex-based extraction without BeautifulSoup
            # Remove script and style tags
            text = re.sub(r'<script[^>]*>.*?</script>', '', html_preview, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)

            # Remove all HTML tags
            text = re.sub(r'<[^>]+>', ' ', text)

            # Decode HTML entities
            text = text.replace('&nbsp;', ' ')
            text = text.replace('&amp;', '&')
            text = text.replace('&lt;', '<')
            text = text.replace('&gt;', '>')
            text = text.replace('&quot;', '"')

            # Clean whitespace
            main_content = re.sub(r'\s+', ' ', text).strip()

    except Exception as e:
        logger.warning("HTML extraction failed: %s", e)
        # Ultimate fallback: strip all tags
        main_content = re.sub(r'<[^>]+>', ' ', html_preview)
        main_content = re.sub(r'\s+', ' ', main_content).strip()

    # Bound the output
    return main_content[:max_chars]


def extract_structured_snippet(data: str, max_chars: int = 20_000) -> str:
    """
    Extract structured snippet from JSON/text data.

    Args:
        data: Input data (JSON or text)
        max_chars: Maximum characters to return

    Returns:
        Extracted snippet, bounded
    """
    if not data:
        return ""

    data = data[:50_000]  # Truncate for safety

    # Try to parse as JSON
    try:
        import json
        parsed = json.loads(data)

        # Extract meaningful fields
        def extract_values(obj, depth=0):
            if depth > 3:
                return []
            if isinstance(obj, str):
                if len(obj) > 10 and len(obj) < 1000:
                    return [obj]
                return []
            if isinstance(obj, dict):
                result = []
                for key in ['title', 'name', 'description', 'content', 'text', 'body', 'summary', 'snippet']:
                    if key in obj and isinstance(obj[key], str):
                        result.append(obj[key])
                for value in obj.values():
                    result.extend(extract_values(value, depth + 1))
                return result
            if isinstance(obj, list):
                result = []
                for item in obj[:10]:  # Limit list items
                    result.extend(extract_values(item, depth + 1))
                return result
            return []

        values = extract_values(parsed)
        if values:
            snippet = ' | '.join(values[:5])  # Combine up to 5 values
            return snippet[:max_chars]

    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: return truncated text
    return data[:max_chars]


@dataclass
class ExtractedContent:
    """Structured extracted content."""
    url: str
    title: str = ""
    main_content: str = ""
    links: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _extract_title_selectolax(html: str) -> str:
    """Extract title using selectolax (Tier 2 migration)."""
    try:
        tree = SelectoLAXParser(html)
        title = tree.css_first("title")
        if title:
            return title.text(strip=True)
    except Exception:
        pass
    return ""


def _extract_links_selectolax(html: str, base_url: str, max_links: int = 50) -> list[str]:
    """Extract links using selectolax (Tier 2 migration)."""
    try:
        tree = SelectoLAXParser(html)
        links = []
        seen = set()
        for a in tree.css("a[href]"):
            href = a.attributes.get("href", "")
            if href and href not in seen:
                seen.add(href)
                # Skip javascript, mailto, anchor
                if href.startswith(("javascript:", "mailto:", "#")):
                    continue
                links.append(href)
                if len(links) >= max_links:
                    break
        return links
    except Exception:
        return []


def extract_content_bounded(url: str, html: str, max_text_chars: int = 20_000) -> ExtractedContent:
    """
    Extract content from HTML with bounded output.

    Tier 2 migration: selectolax-first for title + links, html_to_text_fast for main_content.
    Falls back to bs4 html.parser only if selectolax unavailable.
    Falls back to regex/stdlib if neither available.

    Args:
        url: Source URL
        html: HTML content
        max_text_chars: Maximum characters for text content

    Returns:
        ExtractedContent with bounded fields
    """
    content = ExtractedContent(url=url)

    if not html:
        return content

    html = html[:100_000]  # Hard limit on input

    try:
        # Tier 2: selectolax-first for title + links
        if SELECTOLAX_AVAILABLE:
            content.title = _extract_title_selectolax(html)
            content.links = _extract_links_selectolax(html, url)
        # bs4 fallback for title + links
        elif BS4_AVAILABLE:
            soup = BeautifulSoup(html, 'html.parser')
            if soup.title:
                content.title = soup.title.string or ""
            for a in soup.find_all('a', href=True)[:50]:
                href = a.get('href', '')
                if href and not href.startswith(('javascript:', 'mailto:', '#')):
                    content.links.append(href)
        else:
            # Regex fallback for title
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            if title_match:
                content.title = title_match.group(1)
            # Regex fallback for links
            for match in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE):
                href = match.group(1)
                if href and not href.startswith(("javascript:", "mailto:", "#")):
                    content.links.append(href)
                    if len(content.links) >= 50:
                        break

        # Main content: html_to_text_fast (selectolax-first) always used
        content.main_content = extract_main_text_from_html(html, max_text_chars)

    except Exception as e:
        logger.warning(f"Content extraction failed for {url}: {e}")

    # Ensure bounds
    content.title = content.title[:500]
    content.main_content = content.main_content[:max_text_chars]
    content.links = content.links[:50]

    return content


# Import-safe check
def _check_import() -> bool:
    """Verify module imports correctly."""
    return True
