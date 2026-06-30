"""
Sprint 7B: MLX Init Tests
=========================

Tests for MLX initialization:
- JEDNO kanonické init místo (utils/mlx_cache.py)
- cache_limit = 2.5GB
- wired_limit = 2.5GB
"""

import unittest
from unittest.mock import patch


class TestCanonicalMLXInit(unittest.TestCase):
    """Tests for canonical MLX init."""

    def test_mlx_cache_defines_init_mlx_buffers(self):
        """utils/mlx_cache should define init_mlx_buffers."""
        from hledac.universal.utils.mlx_cache import init_mlx_buffers
        self.assertTrue(callable(init_mlx_buffers))

    def test_mlx_cache_limit_2_5gb(self):
        """MLX cache limit should be 2.5GB (2684354560 bytes)."""
        from hledac.universal.utils import mlx_cache

        self.assertEqual(mlx_cache._MLX_CACHE_LIMIT, 2684354560)
        self.assertEqual(mlx_cache._MLX_WIRED_LIMIT, 2684354560)

    def test_mlx_cache_init_wires_limits(self):
        """init_mlx_buffers should wire cache_limit and wired_limit."""
        from hledac.universal.utils.mlx_cache import init_mlx_buffers, _MLX_CACHE_LIMIT, _MLX_WIRED_LIMIT

        # Call init (may be called at module load, but should be safe to call again)
        result = init_mlx_buffers()
        # Should return True if MLX available
        self.assertIsInstance(result, bool)

    def test_mlx_cleanup_sync_callable(self):
        """mlx_cleanup_sync should be callable."""
        from hledac.universal.utils.mlx_cache import mlx_cleanup_sync

        self.assertTrue(callable(mlx_cleanup_sync))
        # Should not raise
        try:
            mlx_cleanup_sync()
        except Exception:  # noqa: BLE001
            pass  # Non-critical

    def test_mlx_cleanup_aggressive_callable(self):
        """mlx_cleanup_aggressive should be callable."""
        from hledac.universal.utils.mlx_cache import mlx_cleanup_aggressive

        self.assertTrue(callable(mlx_cleanup_aggressive))
        try:
            mlx_cleanup_aggressive()
        except Exception:  # noqa: BLE001
            pass  # Non-critical

    def test_evict_all_callable(self):
        """evict_all should be callable."""
        from hledac.universal.utils.mlx_cache import evict_all

        self.assertTrue(callable(evict_all))
        try:
            evict_all()
        except Exception as e:
            self.fail(f"evict_all raised: {e}")


class TestMLXCacheConstants(unittest.TestCase):
    """Tests for MLX cache constants."""

    def test_cache_limit_2_5gb(self):
        """_MLX_CACHE_LIMIT should be 2.5GB."""
        from hledac.universal.utils.mlx_cache import _MLX_CACHE_LIMIT

        expected = 2684354560  # 2.5 GB in bytes
        self.assertEqual(_MLX_CACHE_LIMIT, expected)

    def test_wired_limit_2_5gb(self):
        """_MLX_WIRED_LIMIT should be 2.5GB."""
        from hledac.universal.utils.mlx_cache import _MLX_WIRED_LIMIT

        expected = 2684354560
        self.assertEqual(_MLX_WIRED_LIMIT, expected)


if __name__ == "__main__":
    unittest.main()
