"""
tests/test_mlx_bridge_prefetch_reusable.py

ISSUE M-06: Verify _sync_prefetch uses correct MLX API (not mlx_lm.generate).

The bug was using mlx_lm.generate(model=engine.model_path, cache=True) which:
1. Passed string path instead of model instance
2. Used non-existent cache=True parameter

P1-9 FIX: Removed unused _PREFETCH_CACHE module-level cache.
Caches were built but never retrieved during generation, violating the invariant
that stored caches should be used. On M1 8GB, storing 32 KV caches wasted ~1-2GB
of Metal memory. Now computes prefill directly without caching (fire-and-forget).

Run: pytest tests/test_mlx_bridge_prefetch_reusable.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
import importlib
from _core import aclose


class TestMLXBridgePrefetchReusable:
    """Test suite for M-06: mlx_bridge prefetch (P1-9: no caching)."""

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

    def test_prefetch_computes_without_caching(self):
        """
        P1-9 FIX: Verify every call recomputes (no caching).

        Previously, caches were stored but never used. Now we verify
        that every call to _sync_prefetch recomputes the prefill.
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

        # Multiple calls with same prompt should each recompute
        for i in range(3):
            with patch('utils.mlx_memory.get_mlx_memory_pressure', return_value=(0.5, "normal")):
                with patch('mlx_lm.models.cache.make_prompt_cache', return_value=mock_cache) as mock_make:
                    with patch('mlx.core.eval', return_value=None):
                        with patch('mlx.core.array', return_value=MagicMock()):
                            result = mlx_bridge_mod._sync_prefetch(mock_engine, "same prompt")

            assert result == "same prompt", f"Call {i+1}: Should return prompt"
            # Each call should recompute (no caching)
            assert mock_make.call_count == 1, f"Call {i+1}: Should recompute each time"

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
