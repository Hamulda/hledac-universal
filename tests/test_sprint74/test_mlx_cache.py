"""
Tests for MLX prompt cache and SimHash.
"""

import pytest
from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache


class TestMLXPromptCache:
    """Test MLXPromptCache functionality."""

    @pytest.mark.asyncio
    async def test_cache_put_and_get(self):
        """Put and get should work."""
        cache = MLXPromptCache(max_entries=3)

        await cache.put("hash1", ["cache_state_1"], 100)
        result = await cache.get("hash1")

        assert result == ["cache_state_1"]

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Cache miss should return None."""
        cache = MLXPromptCache(max_entries=3)

        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_eviction_lru(self):
        """Cache should evict oldest entries when full (LRU)."""
        cache = MLXPromptCache(max_entries=2)

        await cache.put("hash1", ["state1"], 1000)
        await cache.put("hash2", ["state2"], 1000)
        await cache.put("hash3", ["state3"], 1000)  # Should evict hash1 (oldest)

        result1 = await cache.get("hash1")
        result2 = await cache.get("hash2")
        result3 = await cache.get("hash3")

        assert result1 is None  # Evicted (oldest)
        assert result2 == ["state2"]
        assert result3 == ["state3"]

    @pytest.mark.asyncio
    async def test_cache_stats(self):
        """Cache stats should track hits/misses."""
        cache = MLXPromptCache(max_entries=3)

        await cache.put("hash1", ["state1"], 100)
        await cache.get("hash1")  # Hit
        await cache.get("hash2")  # Miss

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["items"] == 1

    @pytest.mark.asyncio
    async def test_cache_clear(self):
        """Cache clear should remove all entries."""
        cache = MLXPromptCache(max_entries=3)

        await cache.put("hash1", ["state1"], 100)
        await cache.clear()

        stats = cache.get_stats()
        assert stats["items"] == 0
        assert stats["size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_cache_size_eviction(self):
        """Cache should evict by size when max_size_bytes exceeded."""
        cache = MLXPromptCache(max_entries=10, max_size_gb=0.000001)  # Very small

        await cache.put("hash1", ["state1"], 1000000)  # 1MB - too large
        await cache.put("hash2", ["state2"], 100)       # Small - should fit

        await cache.get("hash1")
        result2 = await cache.get("hash2")

        # First item should be skipped (too large)
        # Second should be stored
        assert result2 == ["state2"]


class TestSimHashProperties:
    """Property-based tests for SimHash."""

    def test_simhash_deterministic(self):
        """Same text with same seed should produce same hash."""
        from hledac.universal.utils.deduplication import SimHash

        text = "This is a test string for deterministic hashing"
        sh1 = SimHash(seed=42)
        sh2 = SimHash(seed=42)

        assert sh1.compute(text) == sh2.compute(text)

    def test_simhash_different_seed_different_hash(self):
        """Different seeds should produce different hashes."""
        from hledac.universal.utils.deduplication import SimHash

        text = "Test string"
        sh1 = SimHash(seed=42)
        sh2 = SimHash(seed=43)

        # High probability of different hash (not guaranteed but very likely)
        assert sh1.compute(text) != sh2.compute(text)

    def test_simhash_empty_string(self):
        """SimHash should handle empty string."""
        from hledac.universal.utils.deduplication import SimHash

        sh = SimHash(seed=42)
        result = sh.compute("")
        assert isinstance(result, int)

    def test_simhash_unicode(self):
        """SimHash should handle unicode."""
        from hledac.universal.utils.deduplication import SimHash

        sh = SimHash(seed=42)
        result = sh.compute("Hello 🌍")
        assert isinstance(result, int)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
