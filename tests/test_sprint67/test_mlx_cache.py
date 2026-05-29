"""
Test mlx_cache - Sprint 67
Tests for MLX model cache and semaphore.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest


class TestMLXCache:
    """Tests for MLX cache and semaphore."""

    def test_cache_stats_empty(self):
        """Test initial cache stats."""
        from hledac.universal.utils.mlx_cache import get_cache_stats
        stats = get_cache_stats()
        assert stats["size"] == 0
        assert stats["max"] == 2
        assert stats["models"] == []

    def test_semaphore_creation(self):
        """Test semaphore is created lazily."""
        from hledac.universal.utils.mlx_cache import get_mlx_semaphore
        sem = get_mlx_semaphore()
        assert sem is not None
        assert sem._value == 1

    def test_cache_lock_creation(self):
        """Test cache lock is created lazily."""
        from hledac.universal.utils.mlx_cache import _get_cache_lock
        lock = _get_cache_lock()
        assert lock is not None
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_get_mlx_model_with_mock(self):
        """Test get_mlx_model with mocked mlx_lm."""
        from hledac.universal.utils.mlx_cache import _MLX_CACHE, get_mlx_model

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()

        with patch("mlx_lm.load") as mock_load:
            mock_load.return_value = (mock_model, mock_tokenizer)

            model, tokenizer = await get_mlx_model("test-model")

            assert model is mock_model
            assert tokenizer is mock_tokenizer
            assert "test-model" in _MLX_CACHE
            assert mock_load.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_lru_eviction(self):
        """Test LRU eviction when cache exceeds max."""
        from hledac.universal.utils.mlx_cache import _MLX_CACHE, get_mlx_model

        # Clear cache first
        _MLX_CACHE.clear()

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()

        with patch("mlx_lm.load") as mock_load:
            mock_load.return_value = (mock_model, mock_tokenizer)

            # Add 2 models
            await get_mlx_model("model-a")
            await get_mlx_model("model-b")

            assert "model-a" in _MLX_CACHE
            assert "model-b" in _MLX_CACHE
            assert len(_MLX_CACHE) == 2

            # Add third model - should evict model-a (LRU)
            await get_mlx_model("model-c")

            assert "model-a" not in _MLX_CACHE
            assert "model-b" in _MLX_CACHE
            assert "model-c" in _MLX_CACHE
            assert len(_MLX_CACHE) == 2

    @pytest.mark.asyncio
    async def test_cache_hit_moves_to_end(self):
        """Test cache hit moves item to end (LRU)."""
        from hledac.universal.utils.mlx_cache import _MLX_CACHE, get_mlx_model

        _MLX_CACHE.clear()

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()

        with patch("mlx_lm.load") as mock_load:
            mock_load.return_value = (mock_model, mock_tokenizer)

            # Add models
            await get_mlx_model("model-x")
            await get_mlx_model("model-y")

            # Access model-x (should move to end)
            await get_mlx_model("model-x")

            # Add new model - should evict model-y
            await get_mlx_model("model-z")

            assert "model-x" in _MLX_CACHE
            assert "model-y" not in _MLX_CACHE
            assert "model-z" in _MLX_CACHE

    @pytest.mark.asyncio
    async def test_get_mlx_model_failure(self):
        """Test get_mlx_model handles failure gracefully."""
        from hledac.universal.utils.mlx_cache import get_mlx_model

        with patch("mlx_lm.load") as mock_load:
            mock_load.side_effect = Exception("Load failed")

            model, tokenizer = await get_mlx_model("bad-model")

            assert model is None
            assert tokenizer is None

    def test_clear_cache(self):
        """Test cache clearing."""
        from hledac.universal.utils.mlx_cache import _MLX_CACHE, clear_mlx_cache, get_cache_stats

        # Add something to cache
        _MLX_CACHE["test"] = (MagicMock(), MagicMock())

        clear_mlx_cache()

        stats = get_cache_stats()
        assert stats["size"] == 0


class TestMLXSemaphore:
    """Tests for MLX semaphore limiting."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Test semaphore limits to 1 concurrent operation."""
        from hledac.universal.utils.mlx_cache import get_mlx_semaphore

        sem = get_mlx_semaphore()
        results = []

        async def task(id):
            async with sem:
                results.append(f"start-{id}")
                await asyncio.sleep(0.01)
                results.append(f"end-{id}")

        # Run 3 tasks concurrently
        await asyncio.gather(*[task(i) for i in range(3)])

        # Check serialization happened
        assert len(results) == 6
        # Each start must come before its end
        for i in range(3):
            assert results.index(f"start-{i}") < results.index(f"end-{i}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
