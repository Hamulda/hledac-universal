"""
Content Cleaner Layer - DEPRECATED Wrapper
=======================================

This module is DEPRECATED. Import from `layers.communication` instead:

    from layers.communication import ContentCleaner, SimpleHTMLCleaner, OutputFormat, CleaningResult

This file exists for backward compatibility only and will be removed in a future version.
"""

import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.content_layer is deprecated. Import from layers.communication instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.communication import (
    CleaningResult,
    ContentCleaner,
    OutputFormat,
    ResiliparseCleaner,
    SearchResultItem,
    SimpleHTMLCleaner,
    clean_html_tags,
    clean_search_result_url,
    extract_url_from_duckduckgo_redirect,
    extract_url_from_google_redirect,
    get_content_cleaner,
    parse_duckduckgo_results,
    parse_google_results,
)

__all__ = [
    "ContentCleaner",
    "SimpleHTMLCleaner",
    "OutputFormat",
    "CleaningResult",
    "ResiliparseCleaner",
    "SearchResultItem",
    "clean_html_tags",
    "clean_search_result_url",
    "extract_url_from_duckduckgo_redirect",
    "extract_url_from_google_redirect",
    "get_content_cleaner",
    "parse_duckduckgo_results",
    "parse_google_results",
]
