"""
TestM-08: Model swap invalidates all prompt/KV caches.

Issue M-08 HIGH: Model swap neinvaliduje _system_prompt_cache, _warmup_cache,
_session_cache_pool — stale Metal allocations, potenciální kompatibilita cache ↔ nový model.

Acceptance criteria: test_model_swap_invalidates_caches.py ověří, že po load_model()
jsou všechny cache pools prázdné.

Invariant M-08:
  ├── _prompt_cache == None
  ├── _system_prompt_cache == None
  ├── _warmup_cache == None
  ├── _warmup_prompt_hash == None
  ├── _kv_cache_pool empty
  ├── _session_cache_pool empty
  └── _telemetry_counters['cache_invalidation_count'] >= 1
"""

from unittest.mock import MagicMock

import pytest


class TestModelSwapCacheInvalidation:
    """Test M-08: Model swap invalidates all caches."""

    def test_invalidate_all_prompt_caches_clears_all(self) -> None:
        """Test that _invalidate_all_prompt_caches clears all cache pools."""
        from brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()

        # Set up all caches
        engine._prompt_cache = {"prompt": "cache"}  # type: ignore[assignment]
        engine._system_prompt_cache = {"system": "cache"}  # type: ignore[assignment]
        engine._warmup_cache = {"warmup": "data"}  # type: ignore[assignment]
        engine._warmup_prompt_hash = "test_hash"
        engine._kv_cache_pool.put("kv_key", ("kv_value", 1.0, 1))
        engine._session_cache_pool.put("sess_key", ("sess_value", "prompt", 1.0, 1))
        initial_counter = engine._telemetry_counters.get("cache_invalidation_count", 0)

        # Call the invalidation method
        engine._invalidate_all_prompt_caches("test_reason")

        # Verify all cleared
        assert engine._prompt_cache is None
        assert engine._system_prompt_cache is None
        assert engine._warmup_cache is None
        assert engine._warmup_prompt_hash is None
        assert list(engine._kv_cache_pool.keys()) == [], "_kv_cache_pool should be empty"
        assert list(engine._session_cache_pool.keys()) == [], "_session_cache_pool should be empty"
        assert engine._telemetry_counters.get("cache_invalidation_count", 0) == initial_counter + 1

    def test_telemetry_counter_added_to_init(self) -> None:
        """Test that cache_invalidation_count is initialized in __init__."""
        from brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()

        assert "cache_invalidation_count" in engine._telemetry_counters, (
            "cache_invalidation_count must be in _telemetry_counters"
        )
        assert engine._telemetry_counters["cache_invalidation_count"] == 0, (
            "cache_invalidation_count should initialize to 0"
        )

    @pytest.mark.asyncio
    async def test_load_model_cache_hit_invalidates_all(self) -> None:
        """Test that load_model() with cache hit invalidates all caches."""
        from brain.deephermes3_engine import DeepHermes3Engine

        engine = DeepHermes3Engine()

        # Set up caches with some data
        engine._system_prompt_cache = {"old": "system"}  # type: ignore[assignment]
        engine._warmup_cache = {"old": "warmup"}  # type: ignore[assignment]
        engine._warmup_prompt_hash = "old_hash"
        engine._kv_cache_pool.put("old_key", ("old_value", 1.0, 1))
        engine._session_cache_pool.put("old_session", ("old_sess", "prompt", 1.0, 1))
        initial_counter = engine._telemetry_counters.get("cache_invalidation_count", 0)

        # Mock hermes_cache to return a cached model (cache hit path)
        cached_model = MagicMock()
        cached_tokenizer = MagicMock()
        with pytest.MonkeyPatch.context():
            # Patch hermes_cache at module level
            import brain.deephermes3_engine as dhe

            original_cache = dhe.hermes_cache
            mock_cache = MagicMock()
            mock_cache.get_model.return_value = (cached_model, cached_tokenizer)
            dhe.hermes_cache = lambda: mock_cache

            try:
                result = await engine.load_model("cached-model")
            finally:
                dhe.hermes_cache = original_cache

        assert result is True

        # M-08 Invariants after cache-hit:
        # - _prompt_cache: may be None if make_prompt_cache fails (e.g., mock in unit test)
        #   or may be a real cache object in integration test
        # - _system_prompt_cache, _warmup_cache, _warmup_prompt_hash: None (invalidated)
        # - _kv_cache_pool, _session_cache_pool: empty (invalidated by start invalidation)
        # - cache_invalidation_count: incremented (start invalidation)
        # _prompt_cache behavior depends on whether make_prompt_cache succeeds with the model
        assert engine._system_prompt_cache is None, "_system_prompt_cache should be None after invalidation"
        assert engine._warmup_cache is None, "_warmup_cache should be None after invalidation"
        assert engine._warmup_prompt_hash is None, "_warmup_prompt_hash should be None after invalidation"
        assert list(engine._kv_cache_pool.keys()) == [], "_kv_cache_pool should be empty after invalidation"
        assert list(engine._session_cache_pool.keys()) == [], "_session_cache_pool should be empty after invalidation"
        assert engine._telemetry_counters.get("cache_invalidation_count", 0) >= initial_counter + 1, (
            "cache_invalidation_count should be incremented after start invalidation"
        )


class TestModelLifecycleCacheInvalidation:
    """Test M-08: model_lifecycle.py also triggers cache invalidation."""

    def test_load_model_triggers_invalidation_on_engine(self) -> None:
        """Test that model_lifecycle.load_model() calls _invalidate_all_prompt_caches."""
        from brain import model_lifecycle

        # Create mock engine with _invalidate_all_prompt_caches
        mock_engine = MagicMock()
        mock_engine.model_name = "test-engine"
        mock_engine.load = MagicMock()
        mock_engine._invalidate_all_prompt_caches = MagicMock()

        # Call load_model through lifecycle
        model_lifecycle.load_model(model=mock_engine, model_name="test-engine")

        # Verify invalidation was called before load
        mock_engine._invalidate_all_prompt_caches.assert_called()
        call_args = mock_engine._invalidate_all_prompt_caches.call_args
        assert "model_lifecycle_swap" in call_args[0][0], "Invalidation reason should contain 'model_lifecycle_swap'"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
