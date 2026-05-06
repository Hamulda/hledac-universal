"""
ContentCleaner - HTML to Markdown/JSON Converter
================================================

Memory-efficient HTML cleaning using ReaderLM-v2 via MLX-LM.
Optimized for Apple Silicon (M1/M2/M3) with 8GB RAM.

Features:
    - MLX-LM for efficient inference on Apple Silicon
    - Converts dirty HTML to clean Markdown/JSON
    - Stateless design - releases memory immediately after use
    - Fallback to BeautifulSoup if MLX unavailable

Integration:
    - Pre-processing step before sending content to DeepSeek
    - Reduces token count by removing HTML noise
    - Standardizes content format for LLM processing

Usage:
    cleaner = ContentCleaner()
    markdown = cleaner.clean_html(
        raw_html="<div><p>Hello <b>world</b></p></div>",
        output_format="markdown"
    )
"""

import logging
import re
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# F214OPT-A: selectolax-first HTML→text (used for OutputFormat.TEXT path)
try:
    from hledac.universal.utils.html_text_fast import html_to_text_fast

    HTML_TEXT_FAST_AVAILABLE = True
except ImportError:
    HTML_TEXT_FAST_AVAILABLE = False
    html_to_text_fast = None  # type: ignore[assignment]


class OutputFormat(Enum):
    """Supported output formats."""
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"


@dataclass
class CleaningResult:
    """Result of HTML cleaning."""
    success: bool
    content: str
    format: OutputFormat
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SimpleHTMLCleaner:
    """
    BeautifulSoup-based HTML cleaner as fallback.

    Fast and memory-efficient without ML dependencies.
    """

    def __init__(self):
        """Initialize SimpleHTMLCleaner."""
        self._bs4 = None
        self._init_bs4()

    def _init_bs4(self):
        """Initialize BeautifulSoup lazily."""
        try:
            from bs4 import BeautifulSoup
            self._bs4 = BeautifulSoup
        except ImportError:
            logger.warning("BeautifulSoup not available")

    def _remove_unwanted_tags(self, soup: Any) -> Any:
        """Remove script, style, nav, footer elements."""
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
            tag.decompose()
        return soup

    def _extract_text(self, soup: Any) -> str:
        """Extract clean text from soup."""
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _to_markdown(self, soup: Any) -> str:
        """Convert HTML to Markdown format."""
        lines = []

        for elem in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'a', 'strong', 'em']):
            text = elem.get_text(strip=True)
            if not text:
                continue

            tag = elem.name.lower()

            if tag.startswith('h'):
                level = int(tag[1])
                lines.append(f'{"#" * level} {text}')
            elif tag == 'p':
                lines.append(text)
            elif tag in ['ul', 'ol']:
                lines.append(text)
            elif tag == 'li':
                lines.append(f'- {text}')
            elif tag == 'a':
                href = elem.get('href', '')
                if href:
                    lines.append(f'[{text}]({href})')
                else:
                    lines.append(text)
            elif tag == 'strong':
                lines.append(f'**{text}**')
            elif tag == 'em':
                lines.append(f'*{text}*')

        return '\n\n'.join(lines)

    def _to_json(self, soup: Any) -> str:
        """Convert HTML to structured JSON format."""
        import json

        data = {
            'title': '',
            'headings': [],
            'paragraphs': [],
            'links': [],
            'lists': []
        }

        title = soup.find('h1')
        if title:
            data['title'] = title.get_text(strip=True)

        for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            data['headings'].append({
                'level': int(h.name[1]),
                'text': h.get_text(strip=True)
            })

        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if text and len(text) > 20:
                data['paragraphs'].append(text)

        for a in soup.find_all('a', href=True):
            data['links'].append({
                'text': a.get_text(strip=True),
                'url': a['href']
            })

        for ul in soup.find_all(['ul', 'ol']):
            items = [li.get_text(strip=True) for li in ul.find_all('li')]
            if items:
                data['lists'].append({
                    'type': ul.name,
                    'items': items
                })

        return json.dumps(data, ensure_ascii=False, indent=2)

    def clean(
        self,
        html: str,
        output_format: OutputFormat = OutputFormat.MARKDOWN
    ) -> CleaningResult:
        """
        Clean HTML using BeautifulSoup.

        Args:
            html: Raw HTML string
            output_format: Desired output format

        Returns:
            CleaningResult with cleaned content
        """
        if self._bs4 is None:
            return CleaningResult(
                success=False,
                content="",
                format=output_format,
                error="BeautifulSoup not available"
            )

        # F214OPT-A: selectolax-first for OutputFormat.TEXT
        if output_format == OutputFormat.TEXT and HTML_TEXT_FAST_AVAILABLE:
            try:
                content = html_to_text_fast(html)  # type: ignore[operator]
                return CleaningResult(
                    success=True,
                    content=content,
                    format=output_format,
                    metadata={"method": "html_text_fast"}
                )
            except Exception as e:
                logger.warning("html_to_text_fast failed for TEXT, falling back to BeautifulSoup: %s", e)
                # fall through to BeautifulSoup path

        try:
            soup = self._bs4(html, 'html.parser')
            soup = self._remove_unwanted_tags(soup)

            if output_format == OutputFormat.TEXT:
                content = self._extract_text(soup)
            elif output_format == OutputFormat.MARKDOWN:
                content = self._to_markdown(soup)
            elif output_format == OutputFormat.JSON:
                content = self._to_json(soup)
            else:
                content = self._extract_text(soup)

            return CleaningResult(
                success=True,
                content=content,
                format=output_format,
                metadata={'method': 'beautifulsoup'}
            )

        except Exception as e:
            logger.error(f"BeautifulSoup cleaning failed: {e}")
            return CleaningResult(
                success=False,
                content="",
                format=output_format,
                error=str(e)
            )


class ResiliparseCleaner:
    """
    Ultra-fast HTML cleaner using Resiliparse (C++ optimized).

    Features:
        - Lightning-fast text extraction (C++ backend)
        - Automatic removal of scripts, styles, navigation
        - Best for large-scale content processing
    """

    def __init__(self):
        """Initialize ResiliparseCleaner."""
        logger.info("ResiliparseCleaner initialized")

    def _extract_text(self, html: str) -> str:
        """
        Extract clean text using Resiliparse.

        Args:
            html: Raw HTML string

        Returns:
            Clean text content
        """
        try:
            from resiliparse.extract.html2text import extract_plain_text

            cleaned = extract_plain_text(html)
            return cleaned.strip()

        except Exception as e:
            logger.error(f"Resiliparse extraction failed: {e}")
            return ""

    def clean(
        self,
        html: str,
        output_format: OutputFormat = OutputFormat.TEXT,
        main_content_only: bool = True
    ) -> CleaningResult:
        """
        Clean HTML using Resiliparse.

        Args:
            html: Raw HTML string
            output_format: Desired output format (TEXT or MARKDOWN)
            main_content_only: Extract only main content (ignores nav, footer, etc.)

        Returns:
            CleaningResult with cleaned content
        """
        start_time = __import__('time').time()

        try:
            if main_content_only:
                html = self._extract_main_content(html)

            content = self._extract_text(html)

            elapsed = __import__('time').time() - start_time

            return CleaningResult(
                success=True,
                content=content,
                format=output_format,
                metadata={
                    'method': 'resiliparse',
                    'elapsed_ms': round(elapsed * 1000, 2)
                }
            )

        except Exception as e:
            logger.error(f"Resiliparse cleaning failed: {e}")
            return CleaningResult(
                success=False,
                content="",
                format=output_format,
                error=str(e)
            )

    def _extract_main_content(self, html: str) -> str:
        """
        Extract main content from HTML (removes nav, footer, etc.).

        Args:
            html: Raw HTML

        Returns:
            HTML with only main content
        """
        import re

        html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL)
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL)
        html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL)
        html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

        main_match = re.search(r'<main[^>]*>(.*?)</main>', html, flags=re.DOTALL)
        if main_match:
            return main_match.group(1)

        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, flags=re.DOTALL)
        if body_match:
            return body_match.group(1)

        return html


class ContentCleaner:
    """
    HTML to Markdown/JSON converter using BeautifulSoup.

    Optimized for M1 Silicon (8GB RAM).
    Lightweight, no ML model dependencies.
    """

    def __init__(
        self,
        use_mlx: bool = True,
        fallback_to_bs4: bool = True,
        default_format: OutputFormat = OutputFormat.MARKDOWN
    ):
        """
        Initialize ContentCleaner.

        Args:
            use_mlx: Whether to try MLX model first (deprecated, kept for compatibility)
            fallback_to_bs4: Whether to fall back to BeautifulSoup
            default_format: Default output format
        """
        self._simple_cleaner: Optional[SimpleHTMLCleaner] = None
        self._use_mlx = use_mlx
        self._fallback_to_bs4 = fallback_to_bs4
        self._default_format = default_format

        if fallback_to_bs4:
            self._simple_cleaner = SimpleHTMLCleaner()

        logger.info("ContentCleaner initialized")

    def _build_prompt(self, html: str, output_format: OutputFormat) -> str:
        """
        Build prompt for ReaderLM.

        Args:
            html: HTML to clean
            output_format: Desired output format

        Returns:
            Formatted prompt
        """
        format_instruction = {
            OutputFormat.MARKDOWN: "Convert to clean Markdown",
            OutputFormat.JSON: "Convert to structured JSON",
            OutputFormat.TEXT: "Extract plain text"
        }[output_format]

        return (
            f"HTML:\n{html}\n\n"
            f"Task: {format_instruction}\n\n"
            f"Output:"
        )

    def _simplify_html(self, html: str, max_length: int = 3000) -> str:
        """
        Simplify HTML for model input.

        Args:
            html: Raw HTML
            max_length: Maximum length

        Returns:
            Simplified HTML
        """
        # Remove script, style, head, nav, footer, header, aside
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL)
        html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL)
        html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        
        # Extract main content area if exists
        main_match = re.search(r'<main[^>]*>(.*?)</main>', html, flags=re.DOTALL)
        if main_match:
            html = main_match.group(1)
        else:
            # Fallback to body content
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, flags=re.DOTALL)
            if body_match:
                html = body_match.group(1)

        html = re.sub(r'\s+', ' ', html).strip()

        if len(html) > max_length:
            html = html[:max_length]

        return html

    def clean_html(
        self,
        raw_html: str,
        output_format: Optional[OutputFormat] = None
    ) -> CleaningResult:
        """
        Clean HTML to specified format.

        Args:
            raw_html: Raw HTML string
            output_format: Desired output format (uses default if None)

        Returns:
            CleaningResult with cleaned content
        """
        if output_format is None:
            output_format = self._default_format

        simplified_html = self._simplify_html(raw_html)

        if self._fallback_to_bs4 and self._simple_cleaner:
            return self._simple_cleaner.clean(simplified_html, output_format)

        return CleaningResult(
            success=False,
            content="",
            format=output_format,
            error="No cleaning method available"
        )

    def clean_html_batch(
        self,
        html_list: List[str],
        output_format: Optional[OutputFormat] = None
    ) -> List[CleaningResult]:
        """
        Clean multiple HTML documents.

        Args:
            html_list: List of HTML strings
            output_format: Desired output format

        Returns:
            List of CleaningResults
        """
        return [self.clean_html(html, output_format) for html in html_list]

    def is_mlx_available(self) -> bool:
        """Check if MLX model is available (deprecated, always returns False)."""
        return False

    def get_status(self) -> Dict[str, Any]:
        """
        Get cleaner status.

        Returns:
            Dictionary with status information
        """
        return {
            'use_bs4': self._simple_cleaner is not None,
            'fallback_to_bs4': self._fallback_to_bs4,
            'default_format': self._default_format.value,
        }


_global_cleaner: Optional[ContentCleaner] = None


def get_content_cleaner() -> ContentCleaner:
    """
    Get global ContentCleaner instance.

    Returns:
        ContentCleaner singleton
    """
    global _global_cleaner

    if _global_cleaner is None:
        _global_cleaner = ContentCleaner()

    return _global_cleaner


# =============================================================================
# UTILITY FUNCTIONS (from stealth_crawler.py integration)
# =============================================================================

from urllib.parse import unquote, urlparse, parse_qs


def clean_html_tags(text: str) -> str:
    """
    Remove HTML tags and normalize whitespace.
    
    Lightweight alternative to full HTML parsing for simple cleaning.
    
    Args:
        text: HTML text to clean
        
    Returns:
        Clean text without HTML tags
        
    Example:
        >>> clean_html_tags("<p>Hello <b>world</b></p>")
        'Hello world'
    """
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_url_from_duckduckgo_redirect(url: str) -> Optional[str]:
    """
    Extract actual URL from DuckDuckGo redirect URL.
    
    DuckDuckGo wraps external URLs in their own redirect format:
    /l/?uddg=<encoded_url>
    
    Args:
        url: DuckDuckGo redirect URL
        
    Returns:
        Actual URL or None if not a redirect
        
    Example:
        >>> extract_url_from_duckduckgo_redirect('/l/?uddg=https%3A%2F%2Fexample.com')
        'https://example.com'
    """
    try:
        if url.startswith('/l/?uddg='):
            return unquote(url.split('uddg=')[1].split('&')[0])
        elif url.startswith('http://') or url.startswith('https://'):
            parsed = urlparse(url)
            if parsed.netloc:
                return url
        return None
    except Exception:
        return None


def extract_url_from_google_redirect(url: str) -> Optional[str]:
    """
    Extract actual URL from Google redirect URL.
    
    Google wraps external URLs in /url?q=<encoded_url> format.
    
    Args:
        url: Google redirect URL
        
    Returns:
        Actual URL or None if not a redirect
        
    Example:
        >>> extract_url_from_google_redirect('/url?q=https%3A%2F%2Fexample.com')
        'https://example.com'
    """
    try:
        if url.startswith('/url?'):
            parsed = parse_qs(url[5:])
            actual_url = unquote(parsed.get('q', [''])[0])
            if actual_url.startswith('http'):
                return actual_url
        elif url.startswith('http'):
            return url
        return None
    except Exception:
        return None


def clean_search_result_url(url: str, source: str = "auto") -> Optional[str]:
    """
    Clean search result URL from various search engines.
    
    Automatically detects and extracts actual URLs from search engine
    redirect wrappers.
    
    Args:
        url: Search result URL
        source: Source engine ('duckduckgo', 'google', or 'auto')
        
    Returns:
        Clean URL or None if invalid
        
    Example:
        >>> clean_search_result_url('/l/?uddg=https%3A%2F%2Fexample.com', 'duckduckgo')
        'https://example.com'
    """
    if not url:
        return None
    
    # Auto-detect source
    if source == "auto":
        if '/l/?uddg=' in url or 'duckduckgo' in url:
            source = "duckduckgo"
        elif '/url?' in url and 'google' in str(urlparse(url).netloc):
            source = "google"
    
    # Clean based on source
    if source == "duckduckgo":
        return extract_url_from_duckduckgo_redirect(url)
    elif source == "google":
        return extract_url_from_google_redirect(url)
    else:
        # Try both
        result = extract_url_from_duckduckgo_redirect(url)
        if result:
            return result
        return extract_url_from_google_redirect(url)


# =============================================================================
# SEARCH RESULT PARSERS (from stealth_crawler.py integration)
# =============================================================================

from dataclasses import dataclass
from typing import List


@dataclass
class SearchResultItem:
    """Search result item with metadata."""
    title: str
    url: str
    snippet: str
    source: str
    rank: int = 0


def parse_duckduckgo_results(html: str, num_results: int = 10) -> List[SearchResultItem]:
    """
    Parse DuckDuckGo HTML search results.
    
    Extracts title, URL and snippet from DuckDuckGo HTML response.
    
    Args:
        html: DuckDuckGo HTML response
        num_results: Maximum number of results to return
        
    Returns:
        List of SearchResultItem
        
    Example:
        >>> results = parse_duckduckgo_results(html_content, 5)
        >>> for r in results:
        ...     print(f"{r.title}: {r.url}")
    """
    results = []
    
    # Primary pattern: result with snippet
    pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
        clean_url = extract_url_from_duckduckgo_redirect(url_raw)
        if clean_url:
            results.append(SearchResultItem(
                title=clean_html_tags(title),
                url=clean_url,
                snippet=clean_html_tags(snippet),
                source="duckduckgo",
                rank=i
            ))
    
    # Fallback pattern: result without snippet
    if not results:
        pattern = r'<a[^>]*href="([^"]*)"[^>]*class="result__a"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, html, re.DOTALL)
        
        for i, (url_raw, title) in enumerate(matches[:num_results]):
            clean_url = extract_url_from_duckduckgo_redirect(url_raw)
            if clean_url:
                results.append(SearchResultItem(
                    title=clean_html_tags(title),
                    url=clean_url,
                    snippet="",
                    source="duckduckgo",
                    rank=i
                ))
    
    return results


def parse_google_results(html: str, num_results: int = 10) -> List[SearchResultItem]:
    """
    Parse Google HTML search results.
    
    Extracts title, URL and snippet from Google HTML response.
    
    Args:
        html: Google HTML response
        num_results: Maximum number of results to return
        
    Returns:
        List of SearchResultItem
        
    Example:
        >>> results = parse_google_results(html_content, 5)
        >>> for r in results:
        ...     print(f"{r.title}: {r.url}")
    """
    results = []
    
    pattern = r'<div[^>]*class="g"[^>]*>.*?<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<span[^>]*class="st"[^>]*>(.*?)</span>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
        clean_url = extract_url_from_google_redirect(url_raw)
        if clean_url:
            results.append(SearchResultItem(
                title=clean_html_tags(title),
                url=clean_url,
                snippet=clean_html_tags(snippet),
                source="google",
                rank=i
            ))
    
    return results
