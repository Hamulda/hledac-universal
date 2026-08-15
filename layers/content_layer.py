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
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str
import msgspec
logger = logging.getLogger(__name__)
try:
    from hledac.universal.utils.html_text_fast import html_to_text_fast
    HTML_TEXT_FAST_AVAILABLE = True
except ImportError:
    HTML_TEXT_FAST_AVAILABLE = False
    html_to_text_fast: Any = None

# F4: nh3 Rust HTML sanitizer — 9× faster than BS4, 4 MB RSS, M1-safe.
try:
    import nh3 as _nh3
    NH3_AVAILABLE = True
except ImportError:
    _nh3 = None
    NH3_AVAILABLE = False

class OutputFormat(Enum):
    """Supported output formats."""
    MARKDOWN = 'markdown'
    JSON = 'json'
    TEXT = 'text'

class CleaningResult(msgspec.Struct, gc=False):
    """Sprint F300: msgspec.Struct for HTML cleaning result."""
    success: bool
    content: str
    format: OutputFormat
    metadata: dict[str, Any] | None = None
    error: str | None = None

class SimpleHTMLCleaner:
    """
    HTML cleaner with tiered extraction (F4: nh3 + selectolax).

    Replaces BeautifulSoup4 (~2 MB RSS, ~40 ms cold import) with:
      - nh3 (Rust) for TEXT: 9× faster than BS4, 4 MB RSS, M1-safe
      - selectolax for MARKDOWN/JSON: CSS selectors, pure Python

    Tier-1 extraction order for TEXT:
      1. html_to_text_fast  — selectolax wrapper, fastest
      2. nh3.clean(tags=set()) — F4: Rust sanitizer fallback
    Tier-2 for MARKDOWN / JSON:
      3. selectolax.parser  — CSS selectors
      4. stdlib regex fallback
    """
    __slots__ = tuple(('_parser_class',))

    def __init__(self):
        """Initialize SimpleHTMLCleaner with selectolax."""
        self._parser_class: type | None = None
        self._init_selectolax()

    def _init_selectolax(self) -> None:
        """Initialize selectolax lazily."""
        try:
            from selectolax.parser import HTMLParser
            self._parser_class = HTMLParser
        except ImportError:
            logger.warning('selectolax not available')

    def _remove_unwanted_tags(self, tree: Any) -> Any:
        """Remove script, style, nav, footer elements (no-op — done upstream by _simplify_html)."""
        return tree

    def _extract_text(self, tree: Any) -> str:
        """Extract clean text from selectolax tree."""
        body = tree.css_first('body') or tree
        text = body.text_content(separator=' ', default='')
        return re.sub(r'\s+', ' ', text).strip()

    def _to_markdown(self, tree: Any) -> str:
        """Convert HTML to Markdown format using selectolax CSS selectors."""
        lines: list[str] = []
        for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'a', 'strong', 'em'):
            for node in tree.css(tag):
                text = node.text(strip=True)
                if not text:
                    continue
                if isinstance(tag, str) and tag.startswith('h'):
                    level = int(tag[-1])  # 'h1'→'1', 'h2'→'2', … always safe
                    lines.append(f"{'#' * level} {text}")
                elif tag == 'p':
                    lines.append(text)
                elif tag in ('ul', 'ol'):
                    pass  # structural, handled by li
                elif tag == 'li':
                    lines.append(f'- {text}')
                elif tag == 'a':
                    href = node.attributes.get('href', '')
                    if href:
                        lines.append(f'[{text}]({href})')
                    else:
                        lines.append(text)
                elif tag == 'strong':
                    lines.append(f'**{text}**')
                elif tag == 'em':
                    lines.append(f'*{text}*')
        return '\n\n'.join(lines)

    def _to_json(self, tree: Any) -> str:
        """Convert HTML to structured JSON format using selectolax CSS selectors."""
        data: dict[str, Any] = {'title': '', 'headings': [], 'paragraphs': [], 'links': [], 'lists': []}
        title_node = tree.css_first('h1')
        if title_node is not None:
            data['title'] = title_node.text(strip=True)
        for h in tree.css('h1,h2,h3,h4,h5,h6'):
            level = int(h.tag[1])  # 'h1'→1, 'h2'→2, … always safe since tag is h1-h6
            data['headings'].append({'level': level, 'text': h.text(strip=True)})
        for p in tree.css('p'):
            text = p.text(strip=True)
            if text and len(text) > 20:
                data['paragraphs'].append(text)
        for a in tree.css('a[href]'):
            data['links'].append({'text': a.text(strip=True), 'url': a.attributes['href']})
        for ul in tree.css('ul,ol'):
            items = [li.text(strip=True) for li in ul.css('li') if li.text(strip=True)]
            if items:
                data['lists'].append({'type': ul.tag, 'items': items})
        return _msgspec_dumps_str(data, ensure_ascii=False, indent=2)

    def clean(self, html: str, output_format: OutputFormat=OutputFormat.MARKDOWN) -> CleaningResult:
        """
        Clean HTML using nh3 (tier-1) or selectolax (tier-2).

        Tier-1 extraction order for TEXT:
          1. html_to_text_fast  — selectolax wrapper, fastest
          2. nh3.clean_text()   — F4: Rust sanitizer, 9× faster than BS4, 4 MB RSS
        Tier-2 for MARKDOWN / JSON:
          3. selectolax.parser  — CSS selectors
          4. stdlib regex fallback
        """
        if output_format == OutputFormat.TEXT:
            # Tier-1: html_text_fast (selectolax wrapper)
            if HTML_TEXT_FAST_AVAILABLE:
                try:
                    content = html_to_text_fast(html)
                    return CleaningResult(success=True, content=content, format=output_format, metadata={'method': 'html_text_fast'})
                except Exception as e:
                    logger.warning('html_to_text_fast failed for TEXT, falling back to nh3: %s', e)
            # Tier-1.5: F4 — nh3 Rust sanitizer (9× faster than BS4)
            # nh3.clean(tags=set()) strips ALL tags + scripts/styles → plain text
            if NH3_AVAILABLE:
                try:
                    content = _nh3.clean(html, tags=set())
                    content = re.sub(r'\s+', ' ', content).strip()
                    if content:
                        return CleaningResult(success=True, content=content, format=output_format, metadata={'method': 'nh3'})
                except Exception as e:
                    logger.warning('nh3.clean failed, falling back to selectolax: %s', e)
        # Tier-2: selectolax for MARKDOWN/JSON or as fallback for TEXT
        if self._parser_class is None:
            return CleaningResult(success=False, content='', format=output_format, error='selectolax not available')
        try:
            tree = self._parser_class(html)
            if output_format == OutputFormat.TEXT:
                content = self._extract_text(tree)
            elif output_format == OutputFormat.MARKDOWN:
                content = self._to_markdown(tree)
            elif output_format == OutputFormat.JSON:
                content = self._to_json(tree)
            else:
                content = self._extract_text(tree)
            return CleaningResult(success=True, content=content, format=output_format, metadata={'method': 'selectolax'})
        except Exception as e:
            logger.error(f'selectolax cleaning failed: {e}')
            return CleaningResult(success=False, content='', format=output_format, error=str(e))

class ResiliparseCleaner:
    """
    Ultra-fast HTML cleaner using Resiliparse (C++ optimized).

    Features:
        - Fast text extraction via C++ backend
        - Automatic removal of scripts, styles, navigation
        - Best for large-scale content processing
    """

    def __init__(self):
        """Initialize ResiliparseCleaner."""
        logger.info('ResiliparseCleaner initialized')

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
            logger.error(f'Resiliparse extraction failed: {e}')
            return ''

    def clean(self, html: str, output_format: OutputFormat=OutputFormat.TEXT, main_content_only: bool=True) -> CleaningResult:
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
            return CleaningResult(success=True, content=content, format=output_format, metadata={'method': 'resiliparse', 'elapsed_ms': round(elapsed * 1000, 2)})
        except Exception as e:
            logger.error(f'Resiliparse cleaning failed: {e}')
            return CleaningResult(success=False, content='', format=output_format, error=str(e))

    def _extract_main_content(self, html: str) -> str:
        """
        Extract main content from HTML (removes nav, footer, etc.).

        Args:
            html: Raw HTML

        Returns:
            HTML with only main content
        """
        import re
        html = re.sub('<head[^>]*>.*?</head>', '', html, flags=re.DOTALL)
        html = re.sub('<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub('<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub('<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
        html = re.sub('<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL)
        html = re.sub('<header[^>]*>.*?</header>', '', html, flags=re.DOTALL)
        html = re.sub('<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL)
        html = re.sub('<!--.*?-->', '', html, flags=re.DOTALL)
        main_match = re.search('<main[^>]*>(.*?)</main>', html, flags=re.DOTALL)
        if main_match:
            return main_match.group(1)
        body_match = re.search('<body[^>]*>(.*?)</body>', html, flags=re.DOTALL)
        if body_match:
            return body_match.group(1)
        return html

class ContentCleaner:
    """
    HTML to Markdown/JSON converter using selectolax.

    Optimized for M1 Silicon (8GB RAM).
    Lightweight, no ML model dependencies.
    """
    __slots__ = tuple(('_default_format', '_fallback_to_selectolax', '_simple_cleaner', '_use_mlx'))

    def __init__(self, use_mlx: bool=True, fallback_to_selectolax: bool=True, default_format: OutputFormat=OutputFormat.MARKDOWN):
        """
        Initialize ContentCleaner.

        Args:
            use_mlx: Whether to try MLX model first (deprecated, kept for compatibility)
            fallback_to_selectolax: Whether to fall back to selectolax-based cleaner
            default_format: Default output format
        """
        self._simple_cleaner: SimpleHTMLCleaner | None = None
        self._use_mlx = use_mlx
        self._fallback_to_selectolax = fallback_to_selectolax
        self._default_format = default_format
        if fallback_to_selectolax:
            self._simple_cleaner = SimpleHTMLCleaner()
        logger.info('ContentCleaner initialized')

    def _build_prompt(self, html: str, output_format: OutputFormat) -> str:
        """
        Build prompt for ReaderLM.

        Args:
            html: HTML to clean
            output_format: Desired output format

        Returns:
            Formatted prompt
        """
        format_instruction = {OutputFormat.MARKDOWN: 'Convert to clean Markdown', OutputFormat.JSON: 'Convert to structured JSON', OutputFormat.TEXT: 'Extract plain text'}[output_format]
        return f'HTML:\n{html}\n\nTask: {format_instruction}\n\nOutput:'

    def _simplify_html(self, html: str, max_length: int=3000) -> str:
        """
        Simplify HTML for model input.

        Args:
            html: Raw HTML
            max_length: Maximum length

        Returns:
            Simplified HTML
        """
        html = re.sub('<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub('<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub('<head[^>]*>.*?</head>', '', html, flags=re.DOTALL)
        html = re.sub('<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
        html = re.sub('<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL)
        html = re.sub('<header[^>]*>.*?</header>', '', html, flags=re.DOTALL)
        html = re.sub('<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL)
        html = re.sub('<!--.*?-->', '', html, flags=re.DOTALL)
        main_match = re.search('<main[^>]*>(.*?)</main>', html, flags=re.DOTALL)
        if main_match:
            html = main_match.group(1)
        else:
            body_match = re.search('<body[^>]*>(.*?)</body>', html, flags=re.DOTALL)
            if body_match:
                html = body_match.group(1)
        html = re.sub('\\s+', ' ', html).strip()
        if len(html) > max_length:
            html = html[:max_length]
        return html

    def clean_html(self, raw_html: str, output_format: OutputFormat | None=None) -> CleaningResult:
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
        if self._fallback_to_selectolax and self._simple_cleaner:
            return self._simple_cleaner.clean(simplified_html, output_format)
        return CleaningResult(success=False, content='', format=output_format, error='No cleaning method available')

    def clean_html_batch(self, html_list: list[str], output_format: OutputFormat | None=None) -> list[CleaningResult]:
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

    def get_status(self) -> dict[str, Any]:
        """
        Get cleaner status.

        Returns:
            Dictionary with status information
        """
        return {'use_selectolax': self._simple_cleaner is not None, 'fallback_to_selectolax': self._fallback_to_selectolax, 'default_format': self._default_format.value}
_global_cleaner: ContentCleaner | None = None

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
from urllib.parse import parse_qs, unquote, urlparse

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
    text = re.sub('<[^>]+>', '', text)
    text = re.sub('\\s+', ' ', text)
    return text.strip()

def extract_url_from_duckduckgo_redirect(url: str) -> str | None:
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

def extract_url_from_google_redirect(url: str) -> str | None:
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

def clean_search_result_url(url: str, source: str='auto') -> str | None:
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
    if source == 'auto':
        if '/l/?uddg=' in url or 'duckduckgo' in url:
            source = 'duckduckgo'
        elif '/url?' in url and 'google' in str(urlparse(url).netloc):
            source = 'google'
    if source == 'duckduckgo':
        return extract_url_from_duckduckgo_redirect(url)
    elif source == 'google':
        return extract_url_from_google_redirect(url)
    else:
        result = extract_url_from_duckduckgo_redirect(url)
        if result:
            return result
        return extract_url_from_google_redirect(url)
from dataclasses import dataclass
from _core import aclose

class SearchResultItem(msgspec.Struct, gc=False):
    """Sprint F300: msgspec.Struct for search result item."""
    title: str
    url: str
    snippet: str
    source: str
    rank: int = 0

def parse_duckduckgo_results(html: str, num_results: int=10) -> list[SearchResultItem]:
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
    pattern = '<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>'
    matches = re.findall(pattern, html, re.DOTALL)
    for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
        clean_url = extract_url_from_duckduckgo_redirect(url_raw)
        if clean_url:
            results.append(SearchResultItem(title=clean_html_tags(title), url=clean_url, snippet=clean_html_tags(snippet), source='duckduckgo', rank=i))
    if not results:
        pattern = '<a[^>]*href="([^"]*)"[^>]*class="result__a"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, html, re.DOTALL)
        for i, (url_raw, title) in enumerate(matches[:num_results]):
            clean_url = extract_url_from_duckduckgo_redirect(url_raw)
            if clean_url:
                results.append(SearchResultItem(title=clean_html_tags(title), url=clean_url, snippet='', source='duckduckgo', rank=i))
    return results

def parse_google_results(html: str, num_results: int=10) -> list[SearchResultItem]:
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
    pattern = '<div[^>]*class="g"[^>]*>.*?<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<span[^>]*class="st"[^>]*>(.*?)</span>'
    matches = re.findall(pattern, html, re.DOTALL)
    for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
        clean_url = extract_url_from_google_redirect(url_raw)
        if clean_url:
            results.append(SearchResultItem(title=clean_html_tags(title), url=clean_url, snippet=clean_html_tags(snippet), source='google', rank=i))
    return results