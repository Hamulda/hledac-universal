"""Sprint F205G: Text Analyzer Wiring — probe tests."""

from hledac.universal.text.text_analyzer_facade import (
    TextAnalyzerFacade,
    TextAnalyzerResult,
    TextAnalyzerHint,
    analyze_text,
    get_text_analyzer_facade,
    MAX_TEXT_ANALYZER_BYTES,
    MAX_TEXT_ANALYZER_HINTS,
)

__all__ = [
    "TextAnalyzerFacade",
    "TextAnalyzerResult",
    "TextAnalyzerHint",
    "analyze_text",
    "get_text_analyzer_facade",
    "MAX_TEXT_ANALYZER_BYTES",
    "MAX_TEXT_ANALYZER_HINTS",
]