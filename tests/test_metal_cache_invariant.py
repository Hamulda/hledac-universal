"""
tests/test_metal_cache_invariant.py — ISSUE #3 Metal cache invariant tests

Tests the GHOST_INVARIANT: get_dynamic_metal_cache_limit("normal") >= 512 MiB.

Verifies that:
  1. normal floor >= 512 MiB (not 256 MiB like emergency)
  2. emergency floor == 256 MiB
  3. Both mlx_cache and mlx_memory._core delegate to the same factory
  4. thermal_headroom scaling works correctly
  5. Ceiling is always 1.5 GiB regardless of state

ISSUE #3: Previously mlx_memory._core had hardcoded 256 MiB floor for all states,
causing it to return up to 256 MiB less cache than mlx_cache in normal operation.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


# Canonical constants used in assertions
_512_MIB = 512 * 1024 * 1024
_256_MIB = 256 * 1024 * 1024
_1_5_GIB = int(1.5 * 1024 ** 3)


class TestMetalCacheInvariant(unittest.TestCase):
    """GHOST_INVARIANT: normal floor >= 512 MiB, emergency floor == 256 MiB."""

    def test_normal_floor_at_least_512_mib(self) -> None:
        """GHOST_INVARIANT: get_dynamic_metal_cache_limit('normal') >= 512 MiB."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3  # 8 GiB available
            limit = get_dynamic_metal_cache_limit(uma_state="normal")
            self.assertGreaterEqual(
                limit, _512_MIB,
                f"Normal floor must be >= 512 MiB, got {limit / 1024**2:.1f} MiB",
            )

    def test_emergency_floor_is_256_mib(self) -> None:
        """Emergency floor must be exactly 256 MiB, giving draft model more headroom."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            limit = get_dynamic_metal_cache_limit(uma_state="emergency")
            self.assertEqual(
                limit, _256_MIB,
                f"Emergency floor must be 256 MiB, got {limit / 1024**2:.1f} MiB",
            )

    def test_ceiling_is_1_5_gib(self) -> None:
        """Ceiling is always 1.5 GiB regardless of available memory."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 100 * 1024 ** 3  # 100 GiB
            limit = get_dynamic_metal_cache_limit(uma_state="normal")
            self.assertEqual(
                limit, _1_5_GIB,
                f"Ceiling must be 1.5 GiB, got {limit / 1024**3:.2f} GiB",
            )

    def test_20_percent_of_available(self) -> None:
        """At moderate available memory, cache = 20% of available, clamped to [512MiB, 1.5GiB]."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            # 6 GiB available → 20% = 1.2 GiB (within [512MiB, 1.5GiB])
            mock_vm.return_value.available = 6 * 1024 ** 3
            limit = get_dynamic_metal_cache_limit(uma_state="normal")
            expected = int(6 * 0.20 * 1024 ** 3)
            self.assertEqual(
                limit, expected,
                f"Expected 20% of 6 GiB = {expected / 1024**2:.1f} MiB, got {limit / 1024**2:.1f} MiB",
            )

    def test_thermal_headroom_severe(self) -> None:
        """thermal_headroom < 0.3 → cache *= 0.25."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3  # 8 GiB
            limit = get_dynamic_metal_cache_limit(uma_state="normal", thermal_headroom=0.2)
            # Without thermal: 20% of 8 = 1.6 GiB → clamped to 1.5 GiB
            # With 0.25 factor: 1.5 * 0.25 = 0.375 GiB
            expected = int(_1_5_GIB * 0.25)
            self.assertEqual(
                limit, expected,
                f"Expected 1.5 GiB * 0.25 = {expected / 1024**2:.1f} MiB, got {limit / 1024**2:.1f} MiB",
            )

    def test_thermal_headroom_mild(self) -> None:
        """0.3 <= thermal_headroom < 0.5 → cache *= 0.5."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            limit = get_dynamic_metal_cache_limit(uma_state="normal", thermal_headroom=0.4)
            expected = int(_1_5_GIB * 0.5)
            self.assertEqual(
                limit, expected,
                f"Expected 1.5 GiB * 0.5 = {expected / 1024**2:.1f} MiB, got {limit / 1024**2:.1f} MiB",
            )

    def test_thermal_headroom_nominal(self) -> None:
        """thermal_headroom >= 0.5 → no reduction."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            limit = get_dynamic_metal_cache_limit(uma_state="normal", thermal_headroom=0.8)
            expected = _1_5_GIB
            self.assertEqual(
                limit, expected,
                f"Expected 1.5 GiB (no reduction), got {limit / 1024**2:.1f} MiB",
            )

    def test_emergency_thermal_hard_floor_256_mib(self) -> None:
        """Emergency + severe thermal must still respect 256 MiB hard floor."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            # Very low available memory → 20% is tiny → clamped to 256 MiB floor
            mock_vm.return_value.available = 512 * 1024 ** 2  # 512 MiB
            limit = get_dynamic_metal_cache_limit(uma_state="emergency", thermal_headroom=0.1)
            self.assertGreaterEqual(
                limit, _256_MIB,
                f"Hard floor 256 MiB must be respected, got {limit / 1024**2:.1f} MiB",
            )

    def test_mlx_cache_delegates_to_factory(self) -> None:
        """mlx_cache.get_dynamic_metal_cache_limit must use the canonical factory."""
        from hledac.universal.utils import mlx_cache

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            result = mlx_cache.get_dynamic_metal_cache_limit(uma_state="normal")
            self.assertEqual(result, _1_5_GIB)

    def test_mlx_memory_delegates_to_factory(self) -> None:
        """mlx_memory._core.get_dynamic_metal_cache_limit must use the canonical factory."""
        from hledac.universal.utils.mlx_memory._core import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            result = get_dynamic_metal_cache_limit(uma_state="normal")
            self.assertEqual(result, _1_5_GIB)

    def test_both_paths_produce_equal_results(self) -> None:
        """
        ISSUE #3 KEY INVARIANT: both paths must produce identical results.

        Previously mlx_memory._core returned 256 MiB floor for normal state,
        while mlx_cache returned 512 MiB floor. This test ensures they now match.
        """
        from hledac.universal.utils import mlx_cache
        from hledac.universal.utils.mlx_memory._core import get_dynamic_metal_cache_limit

        test_cases = [
            ("normal", None, 1.0),
            ("emergency", None, 1.0),
            ("normal", None, 0.2),
            ("normal", None, 0.4),
            ("emergency", None, 0.2),
        ]

        for uma_state, _, thermal in test_cases:
            with self.subTest(uma_state=uma_state, thermal=thermal):
                with patch("psutil.virtual_memory") as mock_vm:
                    mock_vm.return_value.available = 8 * 1024 ** 3
                    mlx_cache_result = mlx_cache.get_dynamic_metal_cache_limit(
                        uma_state=uma_state, thermal_headroom=thermal
                    )
                    core_result = get_dynamic_metal_cache_limit(
                        uma_state=uma_state, thermal_headroom=thermal
                    )
                    self.assertEqual(
                        mlx_cache_result, core_result,
                        f"mlx_cache ({mlx_cache_result}) != mlx_memory._core ({core_result}) "
                        f"for uma_state={uma_state}, thermal={thermal}",
                    )

    def test_all_uma_states_are_valid(self) -> None:
        """All UMA state strings must be handled without raising."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        valid_states = ("ok", "soft_warn", "warn", "critical", "emergency", None)
        with patch("psutil.virtual_memory") as mock_vm:
            mock_vm.return_value.available = 8 * 1024 ** 3
            for state in valid_states:
                with self.subTest(state=state):
                    try:
                        limit = get_dynamic_metal_cache_limit(uma_state=state)
                        self.assertIsInstance(limit, int)
                        self.assertGreater(limit, 0)
                    except Exception as e:
                        self.fail(f"get_dynamic_metal_cache_limit raised for uma_state={state}: {e}")

    def test_psutil_fallback_returns_ceiling(self) -> None:
        """When psutil fails, factory must return the 1.5 GiB ceiling fallback."""
        from hledac.universal.utils.m1_resource import get_dynamic_metal_cache_limit

        with patch("psutil.virtual_memory", side_effect=OSError("simulated psutil failure")):
            limit = get_dynamic_metal_cache_limit(uma_state="normal")
            self.assertEqual(
                limit, _1_5_GIB,
                f"Fallback must be 1.5 GiB ceiling, got {limit / 1024**2:.1f} MiB",
            )


if __name__ == "__main__":
    unittest.main()
