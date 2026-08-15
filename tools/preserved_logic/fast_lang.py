"""
LanguageDetector — fast language detection stub.

Provides ultra-fast language detection for text.
This is a fail-safe stub: all methods return safe defaults.
"""
from __future__ import annotations

import logging
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)


# Language code to name mapping (subset)
_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "cs": "Czech",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "unknown": "Unknown",
}


class LanguageDetector:
    """
    Language detection with fallback modes.

    This is a stub implementation — raises ImportError on instantiation
    so callers fall back to their own logic.
    """

    def __init__(self, *, fallback_mode: bool = True) -> None:
        raise ImportError(
            "LanguageDetector requires fast-langdetect — install with: uv add fast-langdetect"
        )

    def detect(self, text: str, *, min_length: int = 10) -> str:
        """Always returns 'unknown'."""
        return "unknown"

    def get_language_name(self, lang_code: str) -> str:
        return _LANG_NAMES.get(lang_code, "Unknown")

    def is_supported(self, lang_code: str) -> bool:
        return lang_code in _LANG_NAMES
