"""
Tests for EmbeddingRouter (Sprint F228B).

ANE-akcelerovaný embedder routing: ANE → MLX ModernBERT → CPU fallback.
M1 8GB UMA guard: ANE and MLX ModernBERT never loaded simultaneously.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch


class TestEmbeddingRouterSync(unittest.TestCase):
    """Test synchronous _get_embedder_sync() path."""

    def test_router_uses_cached_ane_when_loaded(self):
        """When ANE is already cached and loaded, uses it."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        with patch.object(EmbeddingRouter, '_ensure_initialized'):
            router = EmbeddingRouter()
            router._ane_available = True
            router._initialized = True

            mock_ane = MagicMock()
            mock_ane.is_loaded = True
            router._ane = mock_ane

            with patch.object(router, '_check_mlx_loaded', return_value=False):
                result = router._get_embedder_sync()
                self.assertIs(result, mock_ane)

    def test_router_skips_loading_ane_when_mlx_loaded_and_ane_not_cached(self):
        """MLX in UMA + ANE not cached → uses ModernBERT (don't load ANE when MLX active)."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        with patch.object(EmbeddingRouter, '_ensure_initialized'):
            router = EmbeddingRouter()
            router._ane_available = True
            router._initialized = True
            router._ane = None  # ANE not cached

            mock_mb = MagicMock()
            mock_mb.is_loaded = True
            router._modernbert = mock_mb

            # MLX is loaded, ANE not cached — use ModernBERT
            with patch.object(router, '_check_mlx_loaded', return_value=True):
                with patch.object(router, '_load_modernbert', return_value=mock_mb):
                    result = router._get_embedder_sync()
                    self.assertIs(result, mock_mb)

    def test_router_uses_cached_ane_even_when_mlx_loaded(self):
        """ANE already cached → use ANE even if MLX is in UMA (ANE doesn't add to Metal buffers)."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        with patch.object(EmbeddingRouter, '_ensure_initialized'):
            router = EmbeddingRouter()
            router._ane_available = True
            router._initialized = True

            mock_ane = MagicMock()
            mock_ane.is_loaded = True
            router._ane = mock_ane

            # MLX is also loaded, but ANE is already cached — use ANE
            with patch.object(router, '_check_mlx_loaded', return_value=True):
                result = router._get_embedder_sync()
                self.assertIs(result, mock_ane)

    def test_router_falls_back_to_modernbert_when_ane_not_cached_and_no_mlx(self):
        """Normal path: ANE not cached, MLX not loaded → uses ModernBERT."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        with patch.object(EmbeddingRouter, '_ensure_initialized'):
            router = EmbeddingRouter()
            router._ane_available = True
            router._initialized = True
            router._ane = None

            mock_mb = MagicMock()
            mock_mb.is_loaded = True

            with patch.object(router, '_check_mlx_loaded', return_value=False):
                with patch.object(router, '_load_modernbert', return_value=mock_mb):
                    result = router._get_embedder_sync()
                    self.assertIs(result, mock_mb)


class TestEmbeddingRouterAsync(unittest.TestCase):
    """Test async get_embedder() path."""

    def test_get_embedder_prefers_ane_when_available_and_no_mlx(self):
        """ANE available and loadable, no MLX in UMA → returns ANE."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()
        router._ane_available = True
        router._initialized = True
        router._ane = None

        async def mock_load_ane():
            mock_ane = MagicMock()
            mock_ane.is_loaded = True
            router._ane = mock_ane
            return True

        router._load_ane = mock_load_ane

        with patch.object(router, '_check_mlx_loaded', return_value=False):
            result = asyncio.run(router.get_embedder())
            self.assertIsNotNone(router._ane)
            self.assertIs(result, router._ane)

    def test_get_embedder_falls_back_to_mlx_when_ane_unavailable(self):
        """ANE not available → falls back to MLX ModernBERT."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()
        router._ane_available = False
        router._initialized = True
        router._ane = None

        mock_mb = MagicMock()
        mock_mb.is_loaded = True

        with patch.object(router, '_check_mlx_loaded', return_value=False):
            with patch.object(router, '_load_modernbert', return_value=mock_mb):
                result = asyncio.run(router.get_embedder())
                self.assertIs(result, mock_mb)


class TestEmbeddingRouterM1Guard(unittest.TestCase):
    """Test M1 8GB UMA guard — ANE and MLX not simultaneously loaded."""

    def test_unload_all_clears_both(self):
        """unload_all() releases both ANE and ModernBERT."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()
        mock_ane = MagicMock()
        mock_mb = MagicMock()
        router._ane = mock_ane
        router._modernbert = mock_mb

        router.unload_all()

        self.assertIsNone(router._ane)
        self.assertIsNone(router._modernbert)


class TestEmbeddingRouterMemoryGuard(unittest.TestCase):
    """Test _check_mlx_loaded() memory guard."""

    def test_check_mlx_loaded_false_when_no_model(self):
        """Returns False when no model in memory."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()

        with patch('hledac.universal.brain.model_manager.ModelManager') as mock_mm_class:
            mock_mm = MagicMock()
            mock_mm.get_current_model.return_value = None
            mock_mm_class.instance.return_value = mock_mm

            self.assertFalse(router._check_mlx_loaded())

    def test_check_mlx_loaded_true_when_hermes(self):
        """Returns True when Hermes3 (MLX) model is loaded."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()

        with patch('hledac.universal.brain.model_manager.ModelManager') as mock_mm_class:
            mock_mm = MagicMock()
            mock_mm.get_current_model.return_value = "hermes3"
            mock_mm_class.instance.return_value = mock_mm

            self.assertTrue(router._check_mlx_loaded())

    def test_check_mlx_loaded_false_when_ane(self):
        """Returns False when only ANE (CoreML) is loaded — not MLX."""
        from hledac.universal.embedding_pipeline import EmbeddingRouter

        router = EmbeddingRouter()

        with patch('hledac.universal.brain.model_manager.ModelManager') as mock_mm_class:
            mock_mm = MagicMock()
            mock_mm.get_current_model.return_value = "ane"
            mock_mm_class.instance.return_value = mock_mm

            # "ane" → not MLX, doesn't compete with ANE
            self.assertFalse(router._check_mlx_loaded())


if __name__ == "__main__":
    unittest.main(verbosity=2)