"""
ContentCleaner — HTML cleaning via selectolax+regex.

Provides HTML to Markdown/JSON/text conversion using M1-native tools:
- selectolax (Rust-based, fast, GIL-free)
- regex (stdlib, linear-time)

G1 FIX: Removed beautifulsoup4 dependency — selectolax covers all use cases.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from _core import aclose
logger = logging.getLogger(__name__)

class OutputFormat(Enum):
    """Supported output formats for HTML cleaning."""
    MARKDOWN = 'markdown'
    JSON = 'json'
    TEXT = 'text'

@dataclass(frozen=True, slots=True)
class CleanResult:
    """Result of HTML cleaning operation."""
    success: bool = True
    content: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

class ContentCleaner:
    """
    HTML cleaning and format conversion using selectolax+regex.

    G1 FIX: No external HTML parser deps needed — selectolax is in main deps,
    regex is stdlib. This replaces the old beautifulsoup4 stub.
    """
    __slots__ = ('_parser',)

    def __init__(self) -> None:
        self._parser = None
        try:
            from selectolax.parser import HTMLParser as _Parser
            self._parser = _Parser
        except ImportError:
            self._parser = None

    def clean_html(self, html: str, output_format: OutputFormat) -> CleanResult:
        """Clean HTML to specified format using selectolax or regex fallback."""
        if not html:
            return CleanResult(success=True, content='', metadata={'empty': True})
        try:
            if self._parser is not None:
                tree = self._parser(html)
                for tag in tree.css('script,style,noscript'):
                    tag.decompose()
                body = tree.css_first('body')
                text = body.text(separator=' ', strip=True) if body else tree.text(separator=' ', strip=True)
            else:
                text = self._regex_extract_text(html)
            text = re.sub('\\s+', ' ', text).strip()
            if output_format == OutputFormat.MARKDOWN:
                content = self._html_to_markdown_simple(text)
            elif output_format == OutputFormat.JSON:
                import json
                content = json.dumps({'text': text, 'length': len(text)})
            else:
                content = text
            return CleanResult(success=True, content=content, metadata={'parser': 'selectolax' if self._parser else 'regex'})
        except Exception as e:
            logger.warning(f'ContentCleaner.clean_html failed: {e}')
            return CleanResult(success=False, content='', error=str(e))

    def _regex_extract_text(self, html: str) -> str:
        """Extract text from HTML using regex only (no external deps)."""
        text = re.sub('<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub('<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub('<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub('<[^>]+>', ' ', text)
        return text

    def _html_to_markdown_simple(self, text: str) -> str:
        """Basic HTML entities decode and cleanup for markdown."""
        import html as html_module
        text = html_module.unescape(text)
        return text