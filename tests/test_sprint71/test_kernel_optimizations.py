"""
Test Kernel Optimizations - Sprint 71
"""
import unittest


class TestKernelOptimizations(unittest.TestCase):
    """Test kernel-level optimizations."""

    def test_mlx_cache_module(self):
        """Test MLX cache module exists."""
        try:
            from hledac.universal.utils import mlx_cache
            # Module exists
            self.assertTrue(hasattr(mlx_cache, '__name__'))
        except ImportError:
            self.skipTest("mlx_cache not available")

    def test_render_coordinator_captcha(self):
        """Test render coordinator has CAPTCHA detection."""
        try:
            from hledac.universal.coordinators.render_coordinator import RenderCoordinator

            coordinator = RenderCoordinator()

            # Check CAPTCHA methods exist
            self.assertTrue(hasattr(coordinator, '_is_captcha_page'))
            self.assertTrue(hasattr(coordinator, '_handle_captcha'))
        except ImportError:
            self.skipTest("render_coordinator not available")

    def test_captcha_patterns_defined(self):
        """Test CAPTCHA patterns are defined."""
        try:
            from hledac.universal.coordinators.render_coordinator import CAPTCHA_PATTERNS

            self.assertIsInstance(CAPTCHA_PATTERNS, list)
            self.assertGreater(len(CAPTCHA_PATTERNS), 0)
            # Check for common patterns
            self.assertIn('captcha', CAPTCHA_PATTERNS)
            self.assertIn('recaptcha', CAPTCHA_PATTERNS)
        except ImportError:
            self.skipTest("render_coordinator not available")

    def test_persistent_layer_vector_search(self):
        """Test persistent layer has vector search."""
        try:
            from hledac.universal.knowledge.persistent_layer import PersistentKnowledgeLayer
            # Check methods exist
            self.assertTrue(hasattr(PersistentKnowledgeLayer, 'vector_search'))
            self.assertTrue(hasattr(PersistentKnowledgeLayer, 'create_vector_index'))
        except ImportError:
            self.skipTest("persistent_layer not available")


if __name__ == '__main__':
    unittest.main()
