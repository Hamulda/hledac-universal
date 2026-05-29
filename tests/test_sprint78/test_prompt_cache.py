"""Tests for prompt cache - LRU, trigram similarity, TTL."""
import time

import pytest


class TestPromptCacheBasic:
    """Basic prompt cache tests."""

    def test_cache_initialization(self):
        """Test cache initialization with default values."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(max_entries=100, embedding_dim=128)
        assert cache._max == 100
        assert cache._dim == 128

    def test_cache_empty_init(self):
        """Test empty cache."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache()
        assert len(cache._cache) == 0
        assert cache._max == 500


class TestPromptCacheLRU:
    """Test LRU eviction."""

    def test_lru_eviction_basic(self):
        """Test basic LRU eviction."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(max_entries=2)
        cache.set("key1", "val1")
        cache.set("key2", "val2")
        cache.set("key3", "val3")

        # Only 2 entries should remain
        assert len(cache._cache) <= 2


class TestPromptCacheTrigram:
    """Test trigram-based similarity."""

    def test_trigram_embedding_shape(self):
        """Test trigram embedding has correct dimension."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(embedding_dim=128)
        emb = cache._get_embedding("test prompt")

        assert len(emb) == 128

    def test_trigram_same_text(self):
        """Test same text produces same embedding."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(embedding_dim=64)
        emb1 = cache._get_embedding("hello world")
        emb2 = cache._get_embedding("hello world")

        assert emb1 == emb2


class TestPromptCacheTTL:
    """Test TTL expiry."""

    def test_ttl_expiry(self):
        """Test TTL removes expired entries."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(max_entries=100)
        cache._ttl = 1  # 1 second
        cache.set("key", "value")

        time.sleep(1.1)

        result = cache.get("key")
        assert result is None


class TestPromptCacheExactMatch:
    """Test exact match."""

    def test_exact_match_hit(self):
        """Test exact match returns value."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache()
        cache.set("exact prompt", "exact response")

        result = cache.get("exact prompt")
        assert result == "exact response"

    def test_exact_match_miss(self):
        """Test exact match miss returns None."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache()
        cache.set("prompt", "response")

        result = cache.get("different prompt")
        assert result is None


class TestPromptCacheIntegration:
    """Integration tests."""

    def test_cache_set_get_roundtrip(self):
        """Test set and get roundtrip."""
        from hledac.universal.brain.prompt_cache import PromptCache

        cache = PromptCache(max_entries=100)
        test_pairs = [
            ("query 1", "response 1"),
            ("query 2", "response 2"),
        ]

        for q, r in test_pairs:
            cache.set(q, r)

        for q, r in test_pairs:
            assert cache.get(q) == r


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
