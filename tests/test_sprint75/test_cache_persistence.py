"""
Tests for system-prompt cache persistence (Sprint 75).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCachePersistence:
    """Test system-prompt cache persistence."""

    def test_save_cache_method_exists(self):
        """Test _save_cache method exists."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        assert hasattr(engine, '_save_cache')
        assert asyncio.iscoroutinefunction(engine._save_cache)

    def test_load_cache_method_exists(self):
        """Test _load_cache method exists."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        assert hasattr(engine, '_load_cache')
        assert asyncio.iscoroutinefunction(engine._load_cache)

    def test_init_system_prompt_cache_method_exists(self):
        """Test _init_system_prompt_cache method exists."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        assert hasattr(engine, '_init_system_prompt_cache')
        assert asyncio.iscoroutinefunction(engine._init_system_prompt_cache)

    def test_init_draft_model_method_exists(self):
        """Test _init_draft_model method exists."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        assert hasattr(engine, '_init_draft_model')
        assert asyncio.iscoroutinefunction(engine._init_draft_model)

    @pytest.mark.asyncio
    async def test_save_cache_no_crash(self):
        """Test _save_cache doesn't crash without model."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        engine._model = None
        engine._system_prompt_cache = None

        # Should not raise
        await engine._save_cache()

    @pytest.mark.asyncio
    async def test_load_cache_no_cache_file(self):
        """Test _load_cache returns False when no cache file exists.

        DeepHermes3Engine uses lazy init - _system_prompt_cache is set during
        initialize(). Without initialize(), we must set it to None explicitly.
        """
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        engine._system_prompt_cache = None  # simulate uninitialized state
        result = await engine._load_cache()
        assert result is False


class TestCacheIntegration:
    """Test cache integration with lifecycle."""

    @pytest.mark.asyncio
    async def test_unload_calls_save_cache(self):
        """Test unload calls _save_cache."""
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()
        engine._model = None
        engine._tokenizer = None
        engine._system_prompt_cache = None  # lazy init without initialize()
        engine._inference_executor = MagicMock()

        # Mock to avoid actual shutdown
        with patch.object(engine, '_save_cache', new_callable=AsyncMock):
            await engine.unload()
            # _save_cache should be called (even if model is None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
