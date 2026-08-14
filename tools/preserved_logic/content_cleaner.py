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

logger = logging.getLogger(__name__)


class OutputFormat(Enum):
    """Supported output formats for HTML cleaning."""

    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class CleanResult:
    """Result of HTML cleaning operation."""

    success: bool = True
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ContentCleaner:
    """
    HTML cleaning and format conversion using selectolax+regex.

    G1 FIX: No external HTML parser deps needed — selectolax is in main deps,
    regex is stdlib. This replaces the old beautifulsoup4 stub.
    """

    def __init__(self) -> None:
        # G1 FIX: Try selectolax first, fall back to regex-only
        self._parser = None
        try:
            from selectolax.parser import HTMLParser as _Parser
            self._parser = _Parser
        except ImportError:
            # selectolax not available — will use regex-only path
            self._parser = None

    def clean_html(self, html: str, output_format: OutputFormat) -> CleanResult:
        """Clean HTML to specified format using selectolax or regex fallback."""
        if not html:
            return CleanResult(success=True, content="", metadata={"empty": True})

        try:
            if self._parser is not None:
                # Tier 1: selectolax (fast, Rust-based)
                tree = self._parser(html)
                # Remove scripts and styles
                for tag in tree.css("script,style,noscript"):
                    tag.decompose()
                # Get body or whole tree
                body = tree.css_first("body")
                text = (body.text(separator=" ", strip=True) if body 
                       else tree.text(separator=" ", strip=True))
            else:
                # Tier 2: regex fallback (stdlib only)
                text = self._regex_extract_text(html)

            # Collapse whitespace
            text = re.sub(r"\s+", " ", text).strip()

            if output_format == OutputFormat.MARKDOWN:
                content = self._html_to_markdown_simple(text)
            elif output_format == OutputFormat.JSON:
                import json
                content = json.dumps({"text": text, "length": len(text)})
            else:
                content = text

            return CleanResult(success=True, content=content, metadata={"parser": "selectolax" if self._parser else "regex"})
        except Exception as e:
            logger.warning(f"ContentCleaner.clean_html failed: {e}")
            return CleanResult(success=False, content="", error=str(e))

    def _regex_extract_text(self, html: str) -> str:
        """Extract text from HTML using regex only (no external deps)."""
        # Remove scripts and styles
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<noscript[^>]*>.*?</noscript>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Strip all tags
        text = re.sub(r"<[^>]+>", " ", text)
        return text

    def _html_to_markdown_simple(self, text: str) -> str:
        """Basic HTML entities decode and cleanup for markdown."""
        import html as html_module
        text = html_module.unescape(text)
        return text
