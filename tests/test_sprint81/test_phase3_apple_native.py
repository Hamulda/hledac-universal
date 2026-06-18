"""
Tests for Sprint 81 - Fáze 3: Apple-Native Acceleration & MEDIUM Wins
===================================================================

Tests for swap detection, xxhash, thermal-aware scheduling
"""

import pytest


class TestXXHash:
    """Test xxhash integration."""

    def test_fast_hash_import(self):
        """Test fast_hash can be imported."""
        from hledac.universal.tools.url_dedup import fast_hash

        result = fast_hash("test_url")
        assert isinstance(result, str)
        assert result

    def test_fast_hash_deterministic(self):
        """Test fast_hash is deterministic."""
        from hledac.universal.tools.url_dedup import fast_hash

        url = "https://example.com/page"
        hash1 = fast_hash(url)
        hash2 = fast_hash(url)

        assert hash1 == hash2

    def test_fast_hash_unique(self):
        """Test fast_hash produces different hashes for different inputs."""
        from hledac.universal.tools.url_dedup import fast_hash

        hash1 = fast_hash("url1")
        hash2 = fast_hash("url2")

        assert hash1 != hash2


class TestEmbeddingService:
    """Test embedding service availability."""

    def test_lancedb_embed_methods_exist(self):
        """Test LanceDB store has embed methods."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        # Verify methods exist
        assert hasattr(LanceDBIdentityStore, '_embed_single')
        assert hasattr(LanceDBIdentityStore, '_embed_batch')


class TestVisionOCR:
    """Test Vision OCR availability."""

    def test_vision_ocr_import(self):
        """Test VisionOCR can be imported."""
        from hledac.universal.tools.ocr_engine import VisionOCR

        ocr = VisionOCR()
        assert ocr is not None


class TestNEREngine:
    """Test NER engine features."""

    def test_ner_engine_nl_available(self):
        """Test NER engine has NaturalLanguage support."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        assert hasattr(engine, '_nl_available')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
