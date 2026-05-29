"""
Tests for speculative decoding and draft model (Sprint 75).
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


class TestSpeculativeDecoding:
    """Test speculative decoding initialization."""

    def test_draft_model_attributes_exist(self):
        """Test that draft model attributes exist."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert hasattr(engine, '_draft_model_obj')
        assert hasattr(engine, '_draft_model_name')
        assert hasattr(engine, '_speculative_enabled')
        assert hasattr(engine, '_num_draft_tokens')
        assert hasattr(engine, '_supports_stream_generate')
        assert hasattr(engine, '_supports_draft')
        assert hasattr(engine, '_supports_kv_quant')
        assert hasattr(engine, '_kv_cache_stats')

    def test_kv_cache_stats_initialized(self):
        """Test KV cache stats are initialized."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        stats = engine._kv_cache_stats
        assert 'cache_uses' in stats
        assert 'cache_prefills' in stats
        assert 'quantized_count' in stats
        assert stats['cache_prefills'] == 1

    def test_draft_model_disabled_by_default(self):
        """Test speculative decoding is disabled by default."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert engine._speculative_enabled is False

    def test_run_inference_uses_draft_when_enabled(self):
        """Test _run_inference uses draft model when enabled."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        engine._model = MagicMock()
        engine._tokenizer = MagicMock()
        engine._speculative_enabled = True
        engine._draft_model_obj = MagicMock()
        engine._supports_draft = True
        engine._supports_kv_quant = False

        with patch('hledac.universal.brain.hermes3_engine.make_prompt_cache') as mock_cache, \
             patch('mlx_lm.generate') as mock_generate:

            mock_cache.return_value = MagicMock()
            mock_generate.return_value = "test response"

            engine._run_inference("test prompt", 0.3, 100)

            # Verify draft_model was passed
            call_kwargs = mock_generate.call_args.kwargs
            assert 'draft_model' in call_kwargs
            assert 'num_draft_tokens' in call_kwargs

    def test_run_inference_falls_back_without_draft(self):
        """Test _run_inference works without draft model."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        engine._model = MagicMock()
        engine._tokenizer = MagicMock()
        engine._speculative_enabled = False
        engine._draft_model_obj = None
        engine._supports_draft = True
        engine._supports_kv_quant = False

        with patch('hledac.universal.brain.hermes3_engine.make_prompt_cache') as mock_cache, \
             patch('mlx_lm.generate') as mock_generate:

            mock_cache.return_value = MagicMock()
            mock_generate.return_value = "test response"

            engine._run_inference("test prompt", 0.3, 100)

            # Verify no draft_model in kwargs
            call_kwargs = mock_generate.call_args.kwargs
            assert 'draft_model' not in call_kwargs


class TestSystemPromptCache:
    """Test system-prompt cache initialization."""

    def test_system_prompt_attribute_exists(self):
        """Test system prompt attribute exists."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert hasattr(engine, '_system_prompt')

    def test_save_cache_method_exists(self):
        """Test _save_cache method exists."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert hasattr(engine, '_save_cache')
        assert asyncio.iscoroutinefunction(engine._save_cache)

    def test_load_cache_method_exists(self):
        """Test _load_cache method exists."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert hasattr(engine, '_load_cache')
        assert asyncio.iscoroutinefunction(engine._load_cache)

    @pytest.mark.asyncio
    async def test_save_cache_is_safe(self):
        """Test _save_cache doesn't crash."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        # Should not raise
        await engine._save_cache()

    @pytest.mark.asyncio
    async def test_load_cache_returns_bool(self):
        """Test _load_cache returns bool."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        result = await engine._load_cache()
        assert isinstance(result, bool)


class TestKVQuantization:
    """Test KV cache quantization."""

    def test_kv_quant_detection(self):
        """Test KV quantization support detection."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        # Should start as False
        assert engine._supports_kv_quant is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
