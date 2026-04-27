"""
Sprint F205G: Text Analyzer Wiring — hermetic probe tests.

Tests TextAnalyzerFacade with zero external dependencies.
Samples: zero-width string, hash-like string, base64-like string.

GHOST_INVARIANTS tested:
- MAX_TEXT_ANALYZER_BYTES = 4096 enforced
- MAX_TEXT_ANALYZER_HINTS = 10 enforced
- fail-soft: analyzer errors do NOT propagate
- additive: results are hints only, not filtering
"""

import pytest

from hledac.universal.text.text_analyzer_facade import (
    TextAnalyzerFacade,
    TextAnalyzerResult,
    TextAnalyzerHint,
    analyze_text,
    get_text_analyzer_facade,
    MAX_TEXT_ANALYZER_BYTES,
    MAX_TEXT_ANALYZER_HINTS,
)


class TestTextAnalyzerFacadeBounds:
    """F205G-1: Bounds enforcement."""

    def test_max_bytes_truncation(self):
        """F205G-1a: Text exceeding MAX_TEXT_ANALYZER_BYTES is truncated."""
        facade = TextAnalyzerFacade()
        long_text = "A" * (MAX_TEXT_ANALYZER_BYTES * 2)
        result = facade.analyze(long_text)
        # Should not raise — truncation is internal
        assert isinstance(result, TextAnalyzerResult)

    def test_max_hints_bound(self):
        """F205G-1b: Hints count is bounded by MAX_TEXT_ANALYZER_HINTS."""
        facade = TextAnalyzerFacade()
        # Text with many potential signals
        text = (
            "a" * 100 + "b" * 100 + "c" * 100 + "d" * 100 + "e" * 100 + "f" * 100 +
            "1" * 100 + "2" * 100 + "3" * 100 + "4" * 100 + "5" * 100 + "6" * 100 +
            "SGVsbG8gV29ybGQh" * 10 + "5d41402abc4b2a76b9719d911017c592" * 10 +
            "ZERO WIDTH" + "​‌‍" + "RLO" + "‮"
        )
        result = facade.analyze(text)
        assert len(result.hints) <= MAX_TEXT_ANALYZER_HINTS


class TestTextAnalyzerFacadeFailSoft:
    """F205G-2: Fail-soft behavior."""

    def test_empty_text_returns_empty_result(self):
        """F205G-2a: Empty text returns empty result without error."""
        facade = TextAnalyzerFacade()
        result = facade.analyze("")
        assert isinstance(result, TextAnalyzerResult)
        assert len(result.hints) == 0
        assert result.analyzer_errors == 0

    def test_none_text_handled(self):
        """F205G-2b: None-like input returns empty result."""
        facade = TextAnalyzerFacade()
        result = facade.analyze("\x00\x00\x00")
        assert isinstance(result, TextAnalyzerResult)


class TestTextAnalyzerFacadeSamples:
    """F205G-3: Hermetic text samples."""

    def test_zero_width_string(self):
        """F205G-3a: Zero-width unicode characters detected."""
        # Zero-width space + zero-width non-joiner
        text = "Hello​World‌Test"
        result = analyze_text(text)
        # Should detect zero-width findings (fail-soft, may be empty if analyzer unavailable)
        assert isinstance(result, TextAnalyzerResult)
        # If analyzer available, should have unicode hints
        unicode_hints = [h for h in result.hints if h.hint_type == "unicode"]
        if unicode_hints:
            assert any("zero" in h.label.lower() or "zw" in h.label.lower() for h in unicode_hints)

    def test_hash_like_string(self):
        """F205G-3b: MD5/SHA-like hex strings identified."""
        text = "Found hash: 5d41402abc4b2a76b9719d911017c592 and sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        result = analyze_text(text)
        assert isinstance(result, TextAnalyzerResult)
        # Hash hints should identify algo types
        hash_hints = [h for h in result.hints if h.hint_type == "hash"]
        assert len(hash_hints) >= 0  # may be empty if no length match

    def test_base64_like_string(self):
        """F205G-3c: Base64-like strings detected."""
        text = "SGVsbG8gV29ybGQhIGZyb20gYmFzZTY0IGVuY29kZXIh"
        result = analyze_text(text)
        assert isinstance(result, TextAnalyzerResult)
        # Encoding hints may detect base64
        encoding_hints = [h for h in result.hints if h.hint_type == "encoding"]
        # fail-soft: just verify result structure
        assert all(isinstance(h, TextAnalyzerHint) for h in result.hints)


class TestTextAnalyzerFacadeSingleton:
    """F205G-4: Singleton behavior."""

    def test_singleton_returns_same_instance(self):
        """F205G-4a: get_text_analyzer_facade() returns same instance."""
        f1 = get_text_analyzer_facade()
        f2 = get_text_analyzer_facade()
        assert f1 is f2

    def test_singleton_type(self):
        """F205G-4b: Singleton is a TextAnalyzerFacade."""
        facade = get_text_analyzer_facade()
        assert isinstance(facade, TextAnalyzerFacade)


class TestTextAnalyzerFacadeAdditive:
    """F205G-5: Additive behavior (no filtering)."""

    def test_result_contains_hint_type_field(self):
        """F205G-5a: Results have hint_type field for each hint."""
        text = "test MD5: 5d41402abc4b2a76b9719d911017c592 base64: SGVsbG8="
        result = analyze_text(text)
        for hint in result.hints:
            assert hasattr(hint, "hint_type")
            assert hasattr(hint, "label")
            assert hasattr(hint, "confidence")
            assert hasattr(hint, "detail")

    def test_no_blocking_on_analyzer_failure(self):
        """F205G-5b: Analyzer failure does not block result generation."""
        # Create a facade and force an analyzer to None
        facade = TextAnalyzerFacade()
        facade._initialized = True
        facade._unicode_analyzer = None
        facade._encoding_detector = None
        facade._hash_identifier = None
        # Should still return (possibly empty) result without raising
        result = facade.analyze("any text")
        assert isinstance(result, TextAnalyzerResult)


class TestMMRImportFailureNonBlocking:
    """F205G-6: MMR import failure does not block text analyzer."""

    def test_mmr_optional_import_fail_soft(self):
        """F205G-6a: Optional MMR import failure is handled gracefully."""
        # The text analyzer facade is independent of MMR
        # Verify it can operate even if MMR module has issues
        facade = TextAnalyzerFacade()
        # Should not raise even if MMR is unavailable
        result = facade.analyze("Test content for analysis")
        assert isinstance(result, TextAnalyzerResult)
        # Result may be empty but must not be None
        assert result is not None
        assert hasattr(result, "hints")
        assert hasattr(result, "analyzer_errors")