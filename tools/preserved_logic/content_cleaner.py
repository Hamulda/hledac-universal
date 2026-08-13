"""
ContentCleaner — HTML cleaning stub.

Provides HTML to Markdown/JSON/text conversion.

This is a fail-safe stub: raises ImportError on instantiation.
"""
from __future__ import annotations

import logging
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
    HTML cleaning and format conversion.

    This is a stub implementation — raises ImportError on instantiation
    so callers fall back to their own _simple_html_extract logic.
    """

    def __init__(self) -> None:
        raise ImportError(
            "ContentCleaner requires beautifulsoup4 — install with: uv add beautifulsoup4"
        )

    def clean_html(self, html: str, output_format: OutputFormat) -> CleanResult:
        """Clean HTML to specified format. Returns empty result for stub."""
        return CleanResult(success=True, content="", metadata={"stub": True}, error=None)
