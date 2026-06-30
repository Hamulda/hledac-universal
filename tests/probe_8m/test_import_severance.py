"""
Sprint 8M — import-chain severance tests.
Verifies that planning package imports do not pull heavy MLX stack.
"""

import subprocess
import sys
import time
from pathlib import Path

PLANNING_INIT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/planning/__init__.py"
HEAVY_PATTERNS = ("mlx_lm", "transformers", "sklearn", "mamba", "mlx")


def _isolated_heavy_count() -> int:
    """Load __init__.py directly as a module, return heavy module count."""
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('planning', '" + PLANNING_INIT + "')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "before = set(sys.modules)\n"
        "spec.loader.exec_module(mod)\n"
        "new = [m for m in sys.modules if m not in before]\n"
        "heavy = [m for m in new if any(x in m.lower() for x in " + str(HEAVY_PATTERNS) + ")]\n"
        "print('HEAVY=' + str(len(heavy)))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    for line in r.stdout.strip().split("\n"):
        if line.startswith("HEAVY="):
            return int(line.split("=")[1])
    return -1


def _public_api_ok(name: str) -> bool:
    """Test that public API name is accessible via lazy __getattr__."""
    r = subprocess.run(
        [sys.executable, "-c", f"from hledac.universal.planning import {name}; print('OK')"],
        capture_output=True, text=True, timeout=60
    )
    return "OK" in r.stdout


class TestIsolatedInit:
    """T1: Ground truth — isolated __init__.py loads with zero heavy modules."""

    def test_planning_init_isolated_zero_heavy(self):
        count = _isolated_heavy_count()
        assert count == 0, f"isolated __init__ should be 0 heavy, got {count}"

    def test_planning_init_loads_clean(self):
        code = (
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('planning', '" + PLANNING_INIT + "')\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print('CLEAN')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        assert "CLEAN" in r.stdout, f"__init__ load failed: {r.stderr[:200]}"


class TestPublicAPI:
    """T2: All public API names accessible via lazy __getattr__."""

    def test_htn_planner(self):
        assert _public_api_ok("HTNPlanner")

    def test_adaptive_cost_model(self):
        assert _public_api_ok("AdaptiveCostModel")

    def test_anytime_beam_search(self):
        assert _public_api_ok("anytime_beam_search")

    def test_slm_decomposer(self):
        assert _public_api_ok("SLMDecomposer")

    def test_task_cache(self):
        assert _public_api_ok("TaskCache")


class TestBenchmark:
    """T3: Cold import benchmark (package only)."""

    def test_package_cold_import_time(self):
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = subprocess.run(
                [sys.executable, "-c", "__import__('hledac.universal.planning')"],
                capture_output=True, timeout=60
            )
            times.append((time.perf_counter() - t0) * 1000)
        avg_ms = sum(times) / len(times)
        print(f"\npackage cold import avg: {avg_ms:.1f}ms")
        # Should be under 15s (was ~6.3s before, after lazy it should be similar or faster)
        assert avg_ms < 15000, f"package import too slow: {avg_ms:.1f}ms"


class TestBackwardCompat:
    """T4: probe_8i fix — import path uses full hledac.universal.planning.htn_planner."""

    def test_probe_8i_import_path(self):
        """Verify the pre-existing bug fix in probe_8i."""
        probe_path = Path(__file__).parent.parent / "probe_8i" / "test_planner_8i.py"
        if probe_path.exists():
            content = probe_path.read_text()
            assert "hledac.universal.planning.htn_planner" in content or \
                   "from hledac.universal.planning" in content, \
                   "probe_8i should use full import path"
