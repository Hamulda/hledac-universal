"""
Sprint 6B: MLX Cache Limits Tests
=================================

Tests for MLX buffer initialization:
- 1.5GB cache limit set (F266: lowered from 2.5GB for M1 8GB stability)
- 1.5GB wired limit set
- init_mlx_buffers() called at module load
- F265H: EMERGENCY floor = 256 MiB (half of normal 512 MiB floor)
"""

import unittest


class TestMLXCacheLimits(unittest.TestCase):
    """Tests for MLX 1.5GB cache/wired limits."""

    def test_init_mlx_buffers_exists(self):
        """Test init_mlx_buffers function exists."""
        from hledac.universal.utils import mlx_cache
        self.assertTrue(hasattr(mlx_cache, 'init_mlx_buffers'))
        self.assertTrue(callable(mlx_cache.init_mlx_buffers))

    def test_mlx_constants_defined(self):
        """Test MLX cache/wired limit constants are 1.5GB (F266)."""
        from hledac.universal.utils import mlx_cache

        expected = 1610612736  # 1.5GB = 1.5 * 1024**3
        self.assertEqual(mlx_cache._MLX_CACHE_LIMIT, expected)
        self.assertEqual(mlx_cache._MLX_WIRED_LIMIT, expected)

    def test_init_mlx_buffers_is_called(self):
        """Test init_mlx_buffers() is called at module load."""
        import hledac.universal.utils.mlx_cache as mlx_cache_module

        source_file = mlx_cache_module.__file__
        with open(source_file, 'r') as f:
            content = f.read()

        # Should have "init_mlx_buffers()" call at module level
        # The call appears after the function definition, before the decorator
        self.assertIn("init_mlx_buffers()", content)
        # Verify it's called as a statement (not inside a function/class)
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'init_mlx_buffers()' in line:
                # Found at module level - not indented inside a function
                stripped = line.rstrip()
                if stripped and not stripped.startswith('#'):
                    # Should be at column 0 or only whitespace before it
                    self.assertEqual(len(line) - len(line.lstrip()), 0,
                                    f"init_mlx_buffers() found indented at line {i+1}")
                break


class TestMLXCacheInitIntegration(unittest.TestCase):
    """Integration tests for MLX cache init."""

    def test_mlx_initialized_flag_exists(self):
        """Test _MLX_INITIALIZED flag exists."""
        from hledac.universal.utils import mlx_cache
        self.assertTrue(hasattr(mlx_cache, '_MLX_INITIALIZED'))


class TestF265HEmergencyFloor(unittest.TestCase):
    """F265H: EMERGENCY Metal cache floor tests."""

    def test_emergency_floor_constant_defined(self):
        """Test _METAL_CACHE_EMERGENCY_FLOOR_BYTES = 256 MiB."""
        from hledac.universal.utils import mlx_cache

        expected = 268435456  # 256 MiB = 256 * 1024**2
        self.assertEqual(mlx_cache._METAL_CACHE_EMERGENCY_FLOOR_BYTES, expected)

    def test_get_dynamic_metal_cache_limit_accepts_uma_state(self):
        """Test get_dynamic_metal_cache_limit accepts uma_state parameter."""
        from hledac.universal.utils import mlx_cache
        import inspect

        sig = inspect.signature(mlx_cache.get_dynamic_metal_cache_limit)
        self.assertIn('uma_state', sig.parameters)

    def test_reconfigure_metal_cache_limit_exists(self):
        """Test reconfigure_metal_cache_limit function exists."""
        from hledac.universal.utils import mlx_cache
        self.assertTrue(hasattr(mlx_cache, 'reconfigure_metal_cache_limit'))
        self.assertTrue(callable(mlx_cache.reconfigure_metal_cache_limit))

    def test_reconfigure_metal_cache_limit_accepts_uma_state(self):
        """Test reconfigure_metal_cache_limit accepts uma_state parameter."""
        from hledac.universal.utils import mlx_cache
        import inspect

        sig = inspect.signature(mlx_cache.reconfigure_metal_cache_limit)
        self.assertIn('uma_state', sig.parameters)


if __name__ == "__main__":
    unittest.main()
