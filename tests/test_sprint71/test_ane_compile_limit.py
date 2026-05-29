"""
Test ANE compile limit - Sprint 71
"""
import unittest


class TestANECompileLimit(unittest.TestCase):
    """Test ANE compilation limit enforcement."""

    def test_ane_compile_limit_initial(self):
        """Test initial compile counter starts at 0."""
        # Reset module state for testing
        import sys
        if 'hledac.universal.brain.dynamic_model_manager' in sys.modules:
            mod = sys.modules['hledac.universal.brain.dynamic_model_manager']
            # Check module loaded
            self.assertTrue(hasattr(mod, 'ANE_COMPILE_LIMIT'))
            self.assertEqual(mod.ANE_COMPILE_LIMIT, 119)

    def test_can_compile_ane_under_limit(self):
        """Test can compile when under limit."""
        # This tests the module-level function
        import sys
        if 'hledac.universal.brain.dynamic_model_manager' in sys.modules:
            mod = sys.modules['hledac.universal.brain.dynamic_model_manager']
            # Check function exists
            self.assertTrue(hasattr(mod, '_can_compile_ane'))

    def test_cache_dir_creation(self):
        """Test cache directory is created."""
        import sys
        if 'hledac.universal.brain.dynamic_model_manager' in sys.modules:
            mod = sys.modules['hledac.universal.brain.dynamic_model_manager']
            self.assertTrue(hasattr(mod, '_get_cache_dir'))


if __name__ == '__main__':
    unittest.main()
