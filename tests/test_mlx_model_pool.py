"""
Test MLXModelPool - Issue #18
Tests unified LRU model pool for M1 8GB memory management.
"""
import asyncio
import pytest






    MLXModelPool,
    MLXModelPoolConfig,
    ModelEntry,
    get_mlx_model_pool,
    get_pool_stats,
    pool_acquire,
    pool_release,
)


class TestMLXModelPool:
    """Test MLXModelPool functionality."""

    def setup_method(self) -> None:

from _core import aclose        """Reset singleton before each test."""
        MLXModelPool.reset_instance()

    def teardown_method(self) -> None:
        """Reset singleton after each test."""
        MLXModelPool.reset_instance()

    def test_singleton(self) -> None:
        """Test singleton pattern."""
        p1 = get_mlx_model_pool()
        p2 = get_mlx_model_pool()
        assert p1 is p2

    def test_config_defaults(self) -> None:
        """Test default configuration."""
        pool = MLXModelPool()
        assert pool._config.budget_gb == 4.0
        assert pool._config.auto_clear_cache is True
        assert pool.loaded_count == 0

    def test_custom_budget(self) -> None:
        """Test custom budget."""
        pool = MLXModelPool(budget_gb=2.0)
        assert pool._budget_bytes == int(2.0 * 1024**3)

    def test_stats_empty(self) -> None:
        """Test stats when empty."""
        pool = MLXModelPool(budget_gb=4.0)
        stats = pool.get_stats()
        assert stats["budget_gb"] == 4.0
        assert stats["loaded_count"] == 0
        assert stats["total_evictions"] == 0
        assert stats["hit_rate_pct"] == 0

    @pytest.mark.asyncio
    async def test_acquire_sync_loader(self) -> None:
        """Test acquire with sync loader."""
        pool = MLXModelPool(budget_gb=4.0)
        
        class MockModel:
            model_name = "hermes"
        class MockTokenizer:
            pass
        
        def loader():
            return (MockModel(), MockTokenizer())
        
        model, tokenizer = await pool.acquire("test_model", loader)
        assert model is not None
        assert tokenizer is not None
        assert pool.loaded_count == 1

    @pytest.mark.asyncio
    async def test_acquire_hit(self) -> None:
        """Test acquire hit (model already loaded)."""
        pool = MLXModelPool(budget_gb=4.0)
        
        class MockModel:
            model_name = "hermes"
        
        call_count = 0
        def loader():
            nonlocal call_count
            call_count += 1
            return (MockModel(), None)
        
        # First acquire
        await pool.acquire("test", loader)
        assert call_count == 1
        
        # Second acquire should hit cache
        await pool.acquire("test", loader)
        assert call_count == 1  # Not reloaded
        
        stats = pool.get_stats()
        assert stats["hit_rate_pct"] > 0

    @pytest.mark.asyncio
    async def test_release(self) -> None:
        """Test release makes model eligible for eviction."""
        pool = MLXModelPool(budget_gb=4.0)
        
        class MockModel:
            model_name = "hermes"
        
        await pool.acquire("test", lambda: (MockModel(), None))
        await pool.release("test")
        assert pool.loaded_count == 1

    @pytest.mark.asyncio
    async def test_scoped_context_manager(self) -> None:
        """Test scoped context manager."""
        pool = MLXModelPool(budget_gb=4.0)
        
        class MockModel:
            model_name = "hermes"
        
        async with pool.scoped("test", lambda: (MockModel(), None)) as (model, _):
            assert model is not None
        
        assert pool.loaded_count == 1

    def test_estimate_model_size_by_module(self) -> None:
        """Test size estimation by module name."""
        pool = MLXModelPool()
        
        class HermesModule:
            pass
        HermesModule.__module__ = "hledac.universal.brain.deephermes3_engine"
        
        class EmbedModule:
            pass
        EmbedModule.__module__ = "hledac.universal.brain.mlx_embedder"
        
        size_hermes = pool._estimate_model_size(HermesModule(), None)
        size_embed = pool._estimate_model_size(EmbedModule(), None)
        
        assert size_hermes == int(1.75 * 1024**3)
        assert size_embed == int(0.5 * 1024**3)


class TestModuleFunctions:
    """Test module-level convenience functions."""

    def setup_method(self) -> None:
        MLXModelPool.reset_instance()

    def teardown_method(self) -> None:
        MLXModelPool.reset_instance()

    def test_get_pool_stats(self) -> None:
        """Test get_pool_stats."""
        stats = get_pool_stats()
        assert "budget_gb" in stats
        assert "loaded_count" in stats

    @pytest.mark.asyncio
    async def test_pool_acquire_release(self) -> None:
        """Test pool_acquire and pool_release."""
        class MockModel:
            model_name = "test"
        
        await pool_acquire("test", lambda: (MockModel(), None))
        assert get_pool_stats()["loaded_count"] == 1
        
        await pool_release("test")
        assert get_pool_stats()["loaded_count"] == 1
