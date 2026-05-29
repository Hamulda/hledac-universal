"""
Test CoreML Verification - Sprint 71
"""
import unittest


class TestCoreMLVerification(unittest.TestCase):
    """Test CoreML model verification."""

    def test_has_apple_intelligence(self):
        """Test Apple Intelligence detection."""
        # Import and test has_apple_intelligence function
        try:
            from hledac.universal.captcha_solver import has_apple_intelligence
            # Function exists and is callable
            self.assertTrue(callable(has_apple_intelligence))
        except ImportError:
            self.skipTest("captcha_solver not available")

    def test_captcha_solver_cache(self):
        """Test CAPTCHA solver cache functionality."""
        try:
            from hledac.universal.captcha_solver import VisionCaptchaSolver

            solver = VisionCaptchaSolver()
            # Check cache attributes exist
            self.assertTrue(hasattr(solver, 'CACHE_TTL'))
            self.assertEqual(solver.CACHE_TTL, 3600)  # 1 hour
        except ImportError:
            self.skipTest("captcha_solver not available")


if __name__ == '__main__':
    unittest.main()
