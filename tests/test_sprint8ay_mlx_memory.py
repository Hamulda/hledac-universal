"""
Sprint 8AY: MLX Memory Hygiene Helper + Surgical Replacements

Tests:
1. test_mlx_helper_lazy_import_behavior
2. test_mlx_helper_absent_env_safe_via_monkeypatch
3. test_mlx_helper_api_shape
4. test_mlx_helper_mb_conversion_from_mock_bytes
5. test_mlx_memory_pressure_thresholds
6. test_replaced_ao_callsites_are_surgical
7. test_eval_plus_clear_pattern_for_eligible_files
8. test_no_boot_regression
"""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock

# Universal path for subprocess tests
UNIVERSAL_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"


class TestMlxHelperLazyImport(unittest.TestCase):
    """Test that helper does NOT import MLX at module load time."""

    def test_mlx_helper_lazy_import_behavior(self):
        """Helper's own _MLX_AVAILABLE and _mlx_core must stay None until API call.

        Note: transitive imports from other utils modules (e.g. memory_dashboard.py)
        may load mlx.core; this is outside helper's scope. The helper itself must
        remain lazy.
        """
        code = f'''
import sys
sys.path.insert(0, "{UNIVERSAL_ROOT}")
for k in list(sys.modules.keys()):
    if 'mlx' in k.lower():
        del sys.modules[k]

from hledac.universal.utils.mlx_memory import (
    _MLX_AVAILABLE, _mlx_core
)

print(f"_MLX_AVAILABLE={{_MLX_AVAILABLE}}")
print(f"_mlx_core={{_mlx_core}}")

assert _MLX_AVAILABLE is None, f"Helper prematurely set _MLX_AVAILABLE={{_MLX_AVAILABLE}}"
assert _mlx_core is None, f"Helper prematurely set _mlx_core={{_mlx_core}}"
print("LAZY_OK")
'''
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        output = r.stdout.strip()
        self.assertIn("_MLX_AVAILABLE=None", output)
        self.assertIn("_mlx_core=None", output)
        self.assertIn("LAZY_OK", output)


class TestMlxHelperAbsentEnv(unittest.TestCase):
    """Test helper degrades safely when MLX is absent."""

    def test_mlx_helper_absent_env_safe_via_monkeypatch(self):
        """When MLX unavailable, all APIs return safe defaults."""
        import hledac.universal.utils.mlx_memory as mm

        # Reset lazy state
        original_available = mm._MLX_AVAILABLE
        original_core = mm._mlx_core
        mm._MLX_AVAILABLE = False
        mm._mlx_core = None

        try:
            self.assertFalse(mm.clear_mlx_cache())
            self.assertIsNone(mm.get_mlx_active_memory_mb())
            self.assertEqual(mm.get_mlx_memory_pressure(), (0, "UNKNOWN"))
            metrics = mm.get_mlx_memory_metrics()
            self.assertFalse(metrics["available"])
            self.assertEqual(metrics["pressure_level"], "UNKNOWN")
        finally:
            mm._MLX_AVAILABLE = original_available
            mm._mlx_core = original_core


class TestMlxHelperApiShape(unittest.TestCase):
    """Test all helper APIs return correct types."""

    def test_mlx_helper_api_shape(self):
        """Each API returns the documented type."""
        from hledac.universal.utils.mlx_memory import (
            clear_mlx_cache,
            get_mlx_active_memory_mb,
            get_mlx_cache_memory_mb,
            get_mlx_memory_metrics,
            get_mlx_memory_pressure,
            get_mlx_peak_memory_mb,
        )
        result = clear_mlx_cache()
        self.assertIsInstance(result, bool)

        for fn in [get_mlx_active_memory_mb, get_mlx_peak_memory_mb, get_mlx_cache_memory_mb]:
            val = fn()
            self.assertTrue(val is None or isinstance(val, int), f"{fn.__name__} returned {val}")

        pressure = get_mlx_memory_pressure()
        self.assertIsInstance(pressure, tuple)
        self.assertEqual(len(pressure), 2)
        self.assertIsInstance(pressure[0], int)
        self.assertIsInstance(pressure[1], str)
        self.assertIn(pressure[1], ("NORMAL", "WARNING", "CRITICAL", "UNKNOWN"))

        metrics = get_mlx_memory_metrics()
        self.assertIsInstance(metrics, dict)
        for key in ("available", "active_mb", "peak_mb", "cache_mb", "pressure_pct", "pressure_level"):
            self.assertIn(key, metrics)


class TestMlxHelperMbConversion(unittest.TestCase):
    """Test MB conversion from bytes."""

    def test_mlx_helper_mb_conversion_from_mock_bytes(self):
        """_mb functions must use integer division by 1024*1024."""
        code = f'''
import sys
sys.path.insert(0, "{UNIVERSAL_ROOT}")
from unittest.mock import MagicMock

import hledac.universal.utils.mlx_memory as mm

mock_mx = MagicMock()
mock_metal = MagicMock()
mock_metal.get_active_memory.return_value = 5 * 1024 * 1024
mock_metal.get_peak_memory.return_value = 10 * 1024 * 1024
mock_metal.get_cache_memory.return_value = 2 * 1024 * 1024
mock_mx.metal = mock_metal
mock_mx.get_active_memory = mock_metal.get_active_memory
mock_mx.get_peak_memory = mock_metal.get_peak_memory
mock_mx.get_cache_memory = mock_metal.get_cache_memory

mm._MLX_AVAILABLE = True
mm._mlx_core = mock_mx

active = mm.get_mlx_active_memory_mb()
peak = mm.get_mlx_peak_memory_mb()
cache = mm.get_mlx_cache_memory_mb()

print(f"active_mb={{active}}, peak_mb={{peak}}, cache_mb={{cache}}")
'''
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        output = r.stdout.strip()
        self.assertIn("active_mb=5", output)
        self.assertIn("peak_mb=10", output)
        self.assertIn("cache_mb=2", output)


class TestMlxMemoryPressureThresholds(unittest.TestCase):
    """Test memory pressure levels on M1 8GB UMA."""

    def test_mlx_memory_pressure_thresholds(self):
        """Pressure levels: NORMAL<80%, WARNING>=80%, CRITICAL>=90%."""
        import hledac.universal.utils.mlx_memory as mm

        test_cases = [
            (0, "NORMAL"),      # 0% -> NORMAL
            (4999, "NORMAL"),   # 4999/6250 = 79.98% -> 79% < 80% -> NORMAL
            (5624, "WARNING"),  # 5624/6250 = 89.98% -> 89% >= 80% and < 90% -> WARNING
            (5625, "CRITICAL"), # 5625/6250 = 90% >= 90% -> CRITICAL
            (6249, "CRITICAL"), # 6249/6250 = 99.98% -> 99% >= 90% -> CRITICAL
            (7000, "CRITICAL"), # 112% >= 90% -> CRITICAL
        ]

        orig_available = mm._MLX_AVAILABLE
        orig_core = mm._mlx_core
        mm._MLX_AVAILABLE = True
        mm._mlx_core = MagicMock()

        try:
            for active_mb, expected_level in test_cases:
                mock_metal = MagicMock()
                mock_metal.get_active_memory.return_value = active_mb * 1024 * 1024
                mm._mlx_core.metal = mock_metal
                mm._mlx_core.get_active_memory = mock_metal.get_active_memory

                pct, level = mm.get_mlx_memory_pressure()
                self.assertEqual(
                    level, expected_level,
                    f"Failed for active={active_mb}: got {level}, expected {expected_level}"
                )
        finally:
            mm._MLX_AVAILABLE = orig_available
            mm._mlx_core = orig_core


class TestReplacedAoCallsitesSurgical(unittest.TestCase):
    """Verify AO replacements are exactly 1-line surgical substitutions."""

    def test_replaced_ao_callsites_are_surgical(self):
        """Both AO sites replaced with clear_mlx_cache() calls."""
        with open(os.path.join(UNIVERSAL_ROOT, "autonomous_orchestrator.py")) as f:
            source = f.read()

        idx1 = source.find("gc.collect()\n                        clear_mlx_cache()")
        self.assertGreater(idx1, 0, "Site 1 replacement not found")

        idx2 = source.find("# MLX cache clear pokud je dostupný\n        clear_mlx_cache()")
        self.assertGreater(idx2, 0, "Site 2 replacement not found")

        self.assertNotIn("if MLX_AVAILABLE and mx is not None:\n            try:\n                mx.clear_cache()", source)


class TestEvalPlusClearPattern(unittest.TestCase):
    """Test clear_mlx_cache() includes mx.eval([]) before metal.clear_cache."""

    def test_eval_plus_clear_pattern_for_eligible_files(self):
        """clear_mlx_cache() must call gc.collect() + mx.eval([]) + metal.clear_cache()."""
        import inspect

        from hledac.universal.utils.mlx_memory import clear_mlx_cache

        src = inspect.getsource(clear_mlx_cache)
        self.assertIn("gc.collect()", src)
        self.assertIn("mx.eval([])", src)
        self.assertIn("clear_cache()", src)


class TestNoBootRegression(unittest.TestCase):
    """Verify boot import does not regress >0.1s."""

    def test_no_boot_regression(self):
        """Boot import median must stay within 0.1s of 1.011776s baseline."""
        code = r'''
import subprocess, sys, statistics, json
code = r"""
import time
t = time.perf_counter()
import hledac.universal.autonomous_orchestrator
print(f"{time.perf_counter()-t:.6f}")
"""
vals = []
for _ in range(5):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    line = [l for l in r.stdout.strip().split('\n') if l and not l.startswith('Warning')]
    vals.append(float(line[-1]))
median = statistics.median(vals)
baseline = 1.011776
regression = abs(median - baseline)
print(json.dumps({"runs": vals, "median": median, "baseline": baseline, "regression": regression, "pass": regression <= 0.1}))
'''
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        output = r.stdout.strip()
        result = json.loads(output)
        self.assertTrue(
            result["pass"],
            f"Regression {result['regression']:.4f}s exceeds 0.1s. "
            f"Median={result['median']:.4f}s vs baseline={result['baseline']:.4f}s"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
