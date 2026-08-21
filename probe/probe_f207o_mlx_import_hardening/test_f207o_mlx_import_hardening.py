"""
Probe F207O: MLX Optional Import Hardening — Sprint F207O-A

Tests that mlx imports are properly guarded and fail-soft when mlx is unavailable.
Uses subprocess to ensure clean import environment per test.
"""

import subprocess
import sys
import unittest


class TestMLXImportHardening(unittest.IsolatedAsyncioTestCase):
    """Test that all MLX imports are fail-soft when mlx.core is unavailable."""

    def _run_blocked(self, code: str) -> tuple[int, str, str]:
        """Run code in a subprocess with mlx blocked via sys.modules = None."""
        blocked_code = (
            "import sys\n"
            # Pre-block all mlx modules — import of blocked module raises ImportError
            "for _mod in ['mlx', 'mlx.core', 'mlx.nn', 'mlx.optimizers',\n"
            "                'mlx.utils', 'mlx.lm', 'mlx._core', 'mlx._nn']:\n"
            "    sys.modules[_mod] = None\n" + code
        )
        result = subprocess.run([sys.executable, "-c", blocked_code], capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr

    def _assert_mlx_unavailable(
        self,
        module_name: str,
        extra_assertions: list[str] | None = None,
    ) -> tuple[int, str, str]:
        """Run import test and assert mlx is unavailable.

        Args:
            module_name: Full import path, e.g. 'prefetch.ssm_reranker'
            extra_assertions: List of extra assertions like 'nn_none: True'

        Returns:
            Tuple of (returncode, stdout, stderr) from the subprocess.
        """
        # Build assertions based on module
        assertions = [
            '"MLX_AVAILABLE: False\\n"',
            '"mx_none: True\\n"',
        ]
        if extra_assertions:
            assertions.extend(extra_assertions)

        code = f"""
from hledac.universal.{module_name} import {module_name.split(".")[-1]}
print("MLX_AVAILABLE:", {module_name.split(".")[-1]}.MLX_AVAILABLE)
print("mx_none:", {module_name.split(".")[-1]}.mx is None)
"""
        if extra_assertions:
            module_var = module_name.split(".")[-1]
            for extra in extra_assertions:
                attr = extra.split(":")[0].strip()
                code += f'print("{attr}:", {module_var}.{attr} is None)\\n'

        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        for assertion in assertions:
            self.assertIn(assertion, stdout)
        return rc, stdout, stderr

    def _assert_module_imports_without_mlx(
        self,
        module_path: str,
        attr_name: str,
        extra_attrs: list[str] | None = None,
    ) -> tuple[int, str, str]:
        """Helper: assert a module imports successfully when mlx is unavailable.

        Args:
            module_path: Full import path e.g. 'prefetch.ssm_reranker'
            attr_name: Module attribute name e.g. 'ssm_reranker'
            extra_attrs: Additional attributes to check (beyond MLX_AVAILABLE and mx)

        Returns:
            Tuple of (rc, stdout, stderr) for additional assertions.
        """
        code = f"""
from hledac.universal.{module_path} import {attr_name}
print("MLX_AVAILABLE:", {attr_name}.MLX_AVAILABLE)
print("mx_none:", {attr_name}.mx is None)
"""
        if extra_attrs:
            for attr in extra_attrs:
                code += f'print("{attr}_none:", {attr_name}.{attr} is None)\n'

        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        self.assertIn("MLX_AVAILABLE: False\n", stdout)
        self.assertIn("mx_none: True\n", stdout)
        if extra_attrs:
            for attr in extra_attrs:
                self.assertIn(f"{attr}_none: True\n", stdout)
        return rc, stdout, stderr

    def test_ssm_reranker_imports_without_mlx(self) -> None:
        """ssm_reranker.py imports successfully when mlx unavailable."""
        self._assert_module_imports_without_mlx("prefetch.ssm_reranker", "ssm_reranker", extra_attrs=["nn"])

    def test_ssm_reranker_stub_raises_on_instantiation(self) -> None:
        """SSMReranker stub raises ImportError when instantiated without mlx."""
        code = """
from hledac.universal.prefetch import ssm_reranker
try:
    ssm_reranker.SSMReranker()
    print("NO_ERROR")
except ImportError as e:
    print("IMPORT_ERROR:", e)
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        self.assertIn("IMPORT_ERROR:", stdout)

    def test_prefetch_oracle_imports_without_mlx(self) -> None:
        """prefetch_oracle.py imports successfully when mlx unavailable."""
        self._assert_module_imports_without_mlx("prefetch.prefetch_oracle", "prefetch_oracle")

    def test_prefetch_oracle_methods_fail_soft(self) -> None:
        """prefetch_oracle.py methods degrade gracefully without mlx."""
        code = """
from hledac.universal.prefetch import prefetch_oracle
import numpy as np

# Create oracle via __new__ to avoid full __init__ (avoids async init)
oracle = object.__new__(prefetch_oracle.PrefetchOracle)
oracle._current_task_embedding = np.zeros(64, dtype=np.float32)
oracle.rel_engine = None
oracle.pq_index = type("PQ", (), {"centroids": None})()
oracle.cms = type("CMS", (), {})()
oracle._seen_fingerprints = {}
oracle._max_seen = 100

emb = oracle._get_entity_embedding("test_entity")
print("emb_is_numpy:", isinstance(emb, np.ndarray))

features = oracle._extract_features_batch([
    {"url": "http://example.com", "type": "graph", "score": 0.5}
])
print("features_is_numpy:", isinstance(features, np.ndarray))
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Method failed: {stderr}\nstdout: {stdout}")
        self.assertIn("emb_is_numpy: True\n", stdout)
        self.assertIn("features_is_numpy: True\n", stdout)

    def test_qmix_imports_without_mlx(self) -> None:
        """qmix.py imports successfully when mlx unavailable."""
        self._assert_module_imports_without_mlx("rl.qmix", "qmix")

    def test_qmix_stubs_raise_on_instantiation(self) -> None:
        """qmix stubs raise ImportError when instantiated without mlx."""
        code = """
from hledac.universal.rl import qmix
for name, cls in [("QMixer", qmix.QMixer), ("QNetwork", qmix.QNetwork), ("QMIXAgent", qmix.QMIXAgent)]:
    try:
        cls(1, 12)
        print(f"{name}:NO_ERROR")
    except ImportError:
        print(f"{name}:IMPORT_ERROR")
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        for name in ["QMixer", "QNetwork", "QMIXAgent"]:
            self.assertIn(f"{name}:IMPORT_ERROR\n", stdout)

    def test_replay_buffer_imports_without_mlx(self) -> None:
        """replay_buffer.py imports successfully when mlx unavailable."""
        code = """
from hledac.universal.rl import replay_buffer
print("MLX_AVAILABLE:", replay_buffer.MLX_AVAILABLE)
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        self.assertIn("MLX_AVAILABLE: False\n", stdout)

    def test_replay_buffer_sample_numpy_fallback(self) -> None:
        """MARLReplayBuffer.sample() returns numpy arrays when mlx unavailable."""
        code = """
from hledac.universal.rl import replay_buffer
import numpy as np

buf = replay_buffer.MARLReplayBuffer(capacity=100, state_dim=12, n_agents=3)
state = np.zeros(12, dtype=np.float32)
actions = np.zeros(3, dtype=np.int32)
buf.push(state, actions, 0.5, state, False)

sample = buf.sample(1)
print("states_numpy:", isinstance(sample["states"], np.ndarray))
print("rewards_numpy:", isinstance(sample["rewards"], np.ndarray))
print("dones_numpy:", isinstance(sample["dones"], np.ndarray))
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Method failed: {stderr}\nstdout: {stdout}")
        self.assertIn("states_numpy: True\n", stdout)
        self.assertIn("rewards_numpy: True\n", stdout)
        self.assertIn("dones_numpy: True\n", stdout)

    def test_state_extractor_imports_without_mlx(self) -> None:
        """state_extractor.py imports successfully when mlx unavailable."""
        code = """
from hledac.universal.rl import state_extractor
print("MLX_AVAILABLE:", state_extractor.MLX_AVAILABLE)
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        self.assertIn("MLX_AVAILABLE: False\n", stdout)

    def test_state_extractor_extract_numpy_fallback(self) -> None:
        """StateExtractor.extract() returns numpy when mlx unavailable."""
        code = """
from hledac.universal.rl import state_extractor
import numpy as np

extractor = state_extractor.StateExtractor(state_dim=12)
thread_state = {"entity_centrality": 0.5, "novelty": 0.3}
global_state = {"queue_size": 10, "memory_pressure": 0.4}

result = extractor.extract(thread_state, global_state)
print("result_numpy:", isinstance(result, np.ndarray))
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Method failed: {stderr}\nstdout: {stdout}")
        self.assertIn("result_numpy: True\n", stdout)

    def test_task_prioritizer_imports_without_mlx(self) -> None:
        """task_prioritizer.py imports successfully when mlx unavailable."""
        self._assert_module_imports_without_mlx("research.task_prioritizer", "task_prioritizer")

    def test_task_prioritizer_stub_raises_on_instantiation(self) -> None:
        """TaskPrioritizer stub raises ImportError when instantiated without mlx."""
        code = """
from hledac.universal.research import task_prioritizer
try:
    task_prioritizer.TaskPrioritizer()
    print("NO_ERROR")
except ImportError as e:
    print("IMPORT_ERROR:", e)
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}")
        self.assertIn("IMPORT_ERROR:", stdout)

    def test_no_mlx_loaded_in_blocked_env(self) -> None:
        """Verify no real mlx modules are loaded when blocked.

        Note: sys.modules may have mlx keys as None (blocking mechanism),
        but no real mlx functionality can be imported.
        """
        code = """
from hledac.universal.prefetch import ssm_reranker, prefetch_oracle
from hledac.universal.rl import qmix, replay_buffer, state_extractor
from hledac.universal.research import task_prioritizer
from _core import aclose

# Check no REAL (non-None) mlx modules loaded
real_mlx_keys = [m for m in sys.modules
                 if m.startswith('mlx') and sys.modules.get(m) is not None]
print("real_mlx_count:", len(real_mlx_keys))
# Also confirm all our target modules imported successfully
print("ssm_available:", ssm_reranker.MLX_AVAILABLE)
"""
        rc, stdout, stderr = self._run_blocked(code)
        self.assertEqual(rc, 0, f"Import failed: {stderr}\n{stdout}")
        self.assertIn("real_mlx_count: 0\n", stdout)
        self.assertIn("ssm_available: False\n", stdout)


class TestF207CF207DRegression(unittest.TestCase):
    """Regression tests: F207C and F207D patterns still intact."""

    def test_mlx_cache_guards_still_intact(self) -> None:
        """Verify utils.mlx_cache.MLX_AVAILABLE pattern unchanged."""
        from hledac.universal.utils import mlx_cache

        self.assertIn("MLX_AVAILABLE", dir(mlx_cache))

    def test_distillation_engine_guards_pattern_intact(self) -> None:
        """Verify brain.distillation_engine has MLX_AVAILABLE guard."""
        from hledac.universal.brain import distillation_engine

        self.assertIn("MLX_AVAILABLE", dir(distillation_engine))
        self.assertIsInstance(distillation_engine.MLX_AVAILABLE, bool)

    def test_moe_router_guards_still_intact(self) -> None:
        """Verify brain.moe_router guards unchanged."""
        from hledac.universal.brain import moe_router

        self.assertIn("MLX_AVAILABLE", dir(moe_router))


if __name__ == "__main__":
    unittest.main()
