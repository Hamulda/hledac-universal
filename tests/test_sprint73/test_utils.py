"""
Tests for signpost_profiler and mlx_prompt_cache.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestSignpostProfiler:
    """Test signpost profiler."""

    def test_signpost_context_manager(self):
        """Signpost context manager should work without error."""
        from hledac.universal.utils.signpost_profiler import signpost_interval

        # Should not raise even if signpost unavailable
        with signpost_interval("Test", "test_operation"):
            pass

    def test_is_signpost_available(self):
        """Check signpost availability."""
        from hledac.universal.utils.signpost_profiler import is_signpost_available

        # Returns bool (may be False on non-Darwin)
        assert isinstance(is_signpost_available(), bool)

    def test_get_stats(self):
        """Get signpost stats."""
        from hledac.universal.utils.signpost_profiler import get_stats

        stats = get_stats()
        assert "available" in stats
        assert "codes_registered" in stats


class TestMLXPromptCache:
    """Test MLX prompt cache."""

    @pytest.mark.asyncio
    async def test_cache_put_and_get(self):
        """Put and get should work."""
        from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache

        cache = MLXPromptCache(max_entries=3)

        await cache.put("hash1", ["cache_state_1"], 100)
        result = await cache.get("hash1")

        assert result == ["cache_state_1"]

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Cache miss should return None."""
        from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache

        cache = MLXPromptCache(max_entries=3)

        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_eviction(self):
        """Cache should evict oldest entries when full."""
        from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache

        cache = MLXPromptCache(max_entries=2)

        await cache.put("hash1", ["state1"], 100)
        await cache.put("hash2", ["state2"], 100)
        await cache.put("hash3", ["state3"], 100)  # Should evict hash1

        result1 = await cache.get("hash1")
        result2 = await cache.get("hash2")
        result3 = await cache.get("hash3")

        assert result1 is None  # Evicted
        assert result2 == ["state2"]
        assert result3 == ["state3"]

    @pytest.mark.asyncio
    async def test_cache_stats(self):
        """Cache stats should track hits/misses."""
        from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache

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
        from hledac.universal.utils.mlx_prompt_cache import MLXPromptCache

        cache = MLXPromptCache(max_entries=3)

        await cache.put("hash1", ["state1"], 100)
        await cache.clear()

        stats = cache.get_stats()
        assert stats["items"] == 0
        assert stats["size_bytes"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
