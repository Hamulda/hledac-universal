"""
Test for Issue M-05: MemoryLayer uses canonical DeepHermes3Engine.

Verifies that:
1. MemoryLayer._model is the same object reference as DeepHermes3Engine._model
2. No duplicate mlx_lm.load() is called for hermes-3

Acceptance criteria:
    test_memory_layer_uses_canonical_engine: memory_layer._model is deephermes3_engine._model
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.layers.memory_layer import MemoryConfig, MemoryLayer


class TestMemoryLayerUsesCanonicalEngine:
    """M-05: MemoryLayer must share the Hermes-3 model with DeepHermes3Engine."""

    @pytest.mark.asyncio
    async def test_memory_layer_uses_canonical_engine(self) -> None:
        """
        Acceptance: memory_layer._model is deephermes3_engine._model.

        M-05 invariant: When MemoryLayer loads hermes-3 model, it should
        reuse the already-loaded model from DeepHermes3Engine instead of
        calling mlx_lm.load() again.
        """
        # Arrange: mock DeepHermes3Engine with a real-like model object
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_engine = MagicMock()
        mock_engine.model = mock_model
        mock_engine.tokenizer = mock_tokenizer
        mock_engine._ensure_model_loaded = AsyncMock()

        # Act: create MemoryLayer with injected engine
        memory_layer = MemoryLayer(
            config=MemoryConfig(),
            deep_hermes_engine=mock_engine,
        )

        # Trigger _load_model for hermes-3
        result = await memory_layer._load_model("hermes-3")

        # Assert: result['model'] is the SAME object as engine.model
        assert result is not None, "Expected non-None result from _load_model"
        assert result["model"] is mock_model, (
            f'M-05 FAILED: expected result["model"] to be same object as '
            f"deep_hermes_engine.model, got {result['model']!r} != {mock_model!r}"
        )
        assert result["tokenizer"] is mock_tokenizer

        # Assert: mlx_lm.load was NOT called (no duplicate model loading)
        # If this fails, MemoryLayer is still calling mlx_lm.load() directly
        mock_engine._ensure_model_loaded.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_layer_load_model_without_engine(self) -> None:
        """
        MemoryLayer without injected engine should not crash.
        """
        memory_layer = MemoryLayer(config=MemoryConfig(), deep_hermes_engine=None)
        result = await memory_layer._load_model("hermes-3")
        # Should return None (logged error, fail-safe)
        assert result is None

    @pytest.mark.asyncio
    async def test_memory_layer_model_property_is_shared(self) -> None:
        """
        Verify the model returned via _load_model matches engine.model.
        The model is stored in _loaded_models only via _load_models_for_state
        (state machine path), not directly via _load_model.
        """
        mock_model = MagicMock()
        mock_engine = MagicMock()
        mock_engine.model = mock_model
        mock_engine.tokenizer = MagicMock()
        mock_engine._ensure_model_loaded = AsyncMock()

        memory_layer = MemoryLayer(
            config=MemoryConfig(),
            deep_hermes_engine=mock_engine,
        )

        # _load_model is called directly in state transition (stored in _loaded_models
        # by _load_models_for_state caller)
        loaded = await memory_layer._load_model("hermes-3")
        assert loaded is not None
        assert loaded["model"] is mock_model

    @pytest.mark.asyncio
    async def test_mlx_load_not_called_for_hermes(self) -> None:
        """
        M-05 invariant: mlx_lm.load must NOT be called for hermes-3.
        """
        mock_model = MagicMock()
        mock_engine = MagicMock()
        mock_engine.model = mock_model
        mock_engine.tokenizer = MagicMock()
        mock_engine._ensure_model_loaded = AsyncMock()

        memory_layer = MemoryLayer(
            config=MemoryConfig(),
            deep_hermes_engine=mock_engine,
        )

        with patch("mlx_lm.load") as mock_load:
            await memory_layer._load_model("hermes-3")
            mock_load.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_hermes_model_still_returns_none(self) -> None:
        """
        Unknown model names should return None (existing behavior preserved).
        """
        memory_layer = MemoryLayer(config=MemoryConfig(), deep_hermes_engine=MagicMock())
        result = await memory_layer._load_model("unknown-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_engine_not_loaded_triggers_ensure_model_loaded(self) -> None:
        """
        If engine.model is None (not yet loaded), _load_model should
        trigger _ensure_model_loaded() to initialize it.
        """
        mock_engine = MagicMock()
        mock_engine.model = None  # Not yet loaded
        mock_engine.tokenizer = MagicMock()
        mock_engine._ensure_model_loaded = AsyncMock()
        mock_engine._ensure_model_loaded.return_value = None

        memory_layer = MemoryLayer(
            config=MemoryConfig(),
            deep_hermes_engine=mock_engine,
        )

        # Simulate: after _ensure_model_loaded, model is still None (load failed)
        result = await memory_layer._load_model("hermes-3")

        # _ensure_model_loaded should have been called
        mock_engine._ensure_model_loaded.assert_called_once()

        # Result should be None since engine.model was None before and after
        assert result is None

    @pytest.mark.asyncio
    async def test_mount_lazy_resolves_engine_from_context(self) -> None:
        """
        If engine is None at construction but ctx provides 'deephermes3_engine',
        mount() should lazily resolve it.
        """
        mock_model = MagicMock()
        mock_engine = MagicMock()
        mock_engine.model = mock_model
        mock_engine.tokenizer = MagicMock()
        mock_engine._ensure_model_loaded = AsyncMock()

        memory_layer = MemoryLayer(config=MemoryConfig(), deep_hermes_engine=None)

        # Mock ctx with deephermes3_engine
        mock_ctx = MagicMock()
        mock_ctx.get = MagicMock(
            side_effect=lambda key: mock_engine if key in ("deephermes3_engine", "hermes_engine") else None
        )

        await memory_layer.mount(mock_ctx)

        # Verify engine was resolved from context
        assert memory_layer._deep_hermes_engine is mock_engine
        mock_ctx.set.assert_called()
