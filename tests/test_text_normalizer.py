#!/usr/bin/env python3
"""Tests for coordinators.fetch.services.text_normalizer"""

import sys

sys.path.insert(0, ".")

import pytest


# Import the test utilities from conftest
def test_text_normalizer_basic() -> None:
    """Test basic NFC normalization."""
    from coordinators.fetch.services.text_normalizer import (
        TextNormalizerService,
    )

    normalizer = TextNormalizerService()
    assert normalizer is not None
    assert hasattr(normalizer, "is_rust_available")
    assert hasattr(normalizer, "normalize")
    assert hasattr(normalizer, "normalize_batch")


def test_text_normalizer_singleton() -> None:
    """Test singleton pattern."""
    from coordinators.fetch.services.text_normalizer import get_text_normalizer

    s1 = get_text_normalizer()
    s2 = get_text_normalizer()
    assert s1 is s2


def test_text_normalizer_nfc_composed() -> None:
    """Test NFC normalization of already composed text."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    result = normalizer.normalize("café")
    assert result == "café"


def test_text_normalizer_nfc_decomposed() -> None:
    """Test NFC normalization of decomposed text."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    # café with combining acute accent (decomposed)
    decomposed = "cafe\u0301"
    result = normalizer.normalize(decomposed)
    # Should be composed NFC form
    assert result == "café"


def test_text_normalizer_batch() -> None:
    """Test batch normalization."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    texts = ["café", "žluťoučký", "Brno"]
    results = normalizer.normalize_batch(texts)
    assert len(results) == 3
    assert results[0] == "café"
    assert results[1] == "žluťoučký"
    assert results[2] == "Brno"


def test_text_normalizer_batch_empty() -> None:
    """Test batch normalization with empty list."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    results = normalizer.normalize_batch([])
    assert results == []


def test_text_normalizer_czech() -> None:
    """Test Czech characters."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    result = normalizer.normalize("řřž")
    assert result == "řřž"


def test_text_normalizer_ascii_unchanged() -> None:
    """Test that ASCII text is unchanged."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    result = normalizer.normalize("Hello World")
    assert result == "Hello World"


def test_text_normalizer_empty() -> None:
    """Test that empty string returns empty string."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    assert normalizer.normalize("") == ""
    assert normalizer.normalize_batch(["", "test", ""]) == ["", "test", ""]


def test_text_normalizer_mixed() -> None:
    """Test mixed ASCII and Unicode."""
    from coordinators.fetch.services.text_normalizer import TextNormalizerService

    normalizer = TextNormalizerService()
    texts = ["Hello", "World", "café", "日本語"]
    results = normalizer.normalize_batch(texts)
    assert results == ["Hello", "World", "café", "日本語"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
