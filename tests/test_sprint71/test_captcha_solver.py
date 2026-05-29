"""
Test Captcha Solver - Sprint 71
"""
import unittest


class TestCaptchaSolver(unittest.TestCase):
    """Test VisionCaptchaSolver functionality."""

    def test_captcha_solver_init(self):
        """Test CAPTCHA solver initialization."""
        try:
            from hledac.universal.captcha_solver import VisionCaptchaSolver

            solver = VisionCaptchaSolver(
                model_path="/fake/path.mlmodel",
                use_ane=True
            )

            self.assertEqual(solver.model_path, "/fake/path.mlmodel")
            # use_ane depends on coremltools availability
            self.assertIn(solver.use_ane, [True, False])
        except ImportError:
            self.skipTest("captcha_solver not available")

    def test_cache_key_generation(self):
        """Test cache key generation."""
        try:
            from hledac.universal.captcha_solver import VisionCaptchaSolver

            solver = VisionCaptchaSolver()
            key1 = solver._get_cache_key(b"test data")
            key2 = solver._get_cache_key(b"test data")
            key3 = solver._get_cache_key(b"different data")

            # Same data should produce same key
            self.assertEqual(key1, key2)
            # Different data should produce different key
            self.assertNotEqual(key1, key3)
            # Key should be 16 characters (hex)
            self.assertEqual(len(key1), 16)
        except ImportError:
            self.skipTest("captcha_solver not available")

    def test_cache_stats(self):
        """Test cache statistics."""
        try:
            from hledac.universal.captcha_solver import VisionCaptchaSolver

            stats = VisionCaptchaSolver.get_cache_stats()
            self.assertIn('size', stats)
            self.assertIn('max_size', stats)
            self.assertIn('ttl_seconds', stats)
            self.assertEqual(stats['ttl_seconds'], 3600)
        except ImportError:
            self.skipTest("captcha_solver not available")


if __name__ == '__main__':
    unittest.main()
