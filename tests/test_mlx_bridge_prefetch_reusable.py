"""
tests/test_mlx_bridge_prefetch_reusable.py

ISSUE M-06: Verify _sync_prefetch uses correct MLX API (not mlx_lm.generate).

The bug was using mlx_lm.generate(model=engine.model_path, cache=True) which:
1. Passed string path instead of model instance
2. Used non-existent cache=True parameter

The fix uses:
1. make_prompt_cache(engine._model) - creates KV cache
2. engine._model(mx.array([tokens]), cache=cache) - prefill
3. mx.eval(cache) - settle lazy ops
4. Stores in _PREFETCH_CACHE[prompt_hash]

Run: pytest tests/test_mlx_bridge_prefetch_reusable.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
import importlib


class TestMLXBridgePrefetchReusable:
    """Test suite for M-06: mlx_bridge prefetch reusable cache."""

    def test_prefetch_uses_correct_api_no_generate(self):
        """
        ACCEPTANCE: Verify _sync_prefetch does NOT call mlx_lm.generate.

        The old buggy code called mlx_lm.generate(model=engine.model_path, cache=True).
        The fix uses make_prompt_cache + model(mx.array, cache=cache) + mx.eval.
        """
        import brain.mlx_bridge as mlx_bridge_mod
        importlib.reload(mlx_bridge_mod)

        mock_engine = MagicMock()
        mock_engine._model = MagicMock()
        mock_engine._tokenizer = MagicMock()
        mock_engine._tokenizer.apply_chat_template = MagicMock(
            return_value="<|im_start|>user\ntest<|im_end|>\n"
        )
        mock_engine._tokenizer.encode = MagicMock(return_value=[1, 2, 3, 4, 5])

        mock_cache = MagicMock()

        with patch('utils.mlx_memory.get_mlx_memory_pressure', return_value=(0.5, "normal")):
            with patch('mlx_lm.models.cache.make_prompt_cache', return_value=mock_cache) as mock_make:
                with patch('mlx.core.eval', return_value=None) as mock_eval:
                    # Patch mx.array to return a mock that can be called with cache=
                    mock_tokens = MagicMock()
                    with patch('mlx.core.array', return_value=mock_tokens):
                        result = mlx_bridge_mod._sync_prefetch(mock_engine, "test prompt")

        # Should return the prompt on success
        assert result == "test prompt", f"Should return prompt, got: {result!r}"

        # Should use make_prompt_cache (correct API, not mlx_lm.generate)
        assert mock_make.call_count == 1, "Should call make_prompt_cache once"

        # Should call mx.eval to settle lazy ops
        assert mock_eval.call_count >= 1, "Should call mx.eval at least once"

        # Second call with same prompt should use cache
        with patch('utils.mlx_memory.get_mlx_memory_pressure', return_value=(0.5, "normal")):
            with patch('mlx_lm.models.cache.make_prompt_cache', return_value=mock_cache) as mock_make2:
                with patch('mlx.core.eval', return_value=None):
                    with patch('mlx.core.array', return_value=mock_tokens):
                        result2 = mlx_bridge_mod._sync_prefetch(mock_engine, "test prompt")

        assert result2 == "test prompt"
        # On cache HIT, make_prompt_cache should NOT be called again
        assert mock_make2.call_count == 0, "make_prompt_cache should NOT be called on cache HIT"

    def test_prefetch_skipped_on_critical_memory(self):
        """Verify prefetch is skipped when memory pressure is CRITICAL."""
        import brain.mlx_bridge as mlx_bridge_mod
        importlib.reload(mlx_bridge_mod)

        mock_engine = MagicMock()
        mock_engine._model = MagicMock()
        mock_engine._tokenizer = MagicMock()

        with patch('utils.mlx_memory.get_mlx_memory_pressure', return_value=(0.95, "CRITICAL")):
            result = mlx_bridge_mod._sync_prefetch(mock_engine, "any prompt")

        assert result == "", "Should return empty string on CRITICAL memory"
        mock_engine._model.assert_not_called()

    def test_prefetch_skipped_when_model_not_loaded(self):
        """Verify prefetch is skipped when model is None."""
        import brain.mlx_bridge as mlx_bridge_mod
        importlib.reload(mlx_bridge_mod)

        mock_engine = MagicMock()
        mock_engine._model = None
        mock_engine._tokenizer = MagicMock()

        result = mlx_bridge_mod._sync_prefetch(mock_engine, "any prompt")
        assert result == "", "Should return empty string when model not loaded"

    def test_prefetch_lru_eviction_at_capacity(self):
        """Verify LRU eviction when cache reaches max capacity."""
        import brain.mlx_bridge as mlx_bridge_mod
        importlib.reload(mlx_bridge_mod)

        mock_engine = MagicMock()
        mock_engine._model = MagicMock()
        mock_engine._tokenizer = MagicMock()
        mock_engine._tokenizer.apply_chat_template = MagicMock(
            side_effect=lambda *a, **kw: f"formatted: {a[0][1]['content']}"
        )
        mock_engine._tokenizer.encode = MagicMock(return_value=[1, 2, 3])

        mock_cache = MagicMock()

        original_maxsize = mlx_bridge_mod._PREFETCH_CACHE_MAXSIZE
        mlx_bridge_mod._PREFETCH_CACHE_MAXSIZE = 2

        try:
            with patch('utils.mlx_memory.get_mlx_memory_pressure', return_value=(0.5, "normal")):
                with patch('mlx_lm.models.cache.make_prompt_cache', return_value=mock_cache):
                    with patch('mlx.core.eval', return_value=None):
                        mock_tokens = MagicMock()
                        with patch('mlx.core.array', return_value=mock_tokens):
                            for p in ["prompt1", "prompt2", "prompt3"]:
                                result = mlx_bridge_mod._sync_prefetch(mock_engine, p)
                                assert result == p, f"Failed for {p}"

                            # Cache should not exceed maxsize
                            assert len(mlx_bridge_mod._PREFETCH_CACHE) <= mlx_bridge_mod._PREFETCH_CACHE_MAXSIZE
        finally:
            mlx_bridge_mod._PREFETCH_CACHE_MAXSIZE = original_maxsize


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
