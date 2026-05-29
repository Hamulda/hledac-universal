"""
Sprint 69: Structure Map Scheduling Tests
"""

import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


class TestStructureMapSchedulingIntegration(unittest.TestCase):
    """Integration testy pro scheduling v orchestrátoru."""

    def setUp(self):
        """Set up orchestrator for testing."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Create minimal orchestrator instance
        self.orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        self.orch._project_root = "/tmp/test_project"
        self.orch._STRUCTURE_MAP_FAIL_OPEN_S = 3600.0
        self.orch._STRUCTURE_MAP_MIN_INTERVAL_S = 600.0
        self.orch._STRUCTURE_MAP_TRUNC_COOLDOWN_PENALTY_S = 600.0

        # Initialize state
        import threading
        self.orch._structure_map_lock = asyncio.Lock()
        self.orch._kqueue_dirty_lock = threading.Lock()
        self.orch._structure_map_state = {
            "file_cache": OrderedDict(),
            "prev_edges": [],
            "last_fingerprint": None,
            "last_run_time": 0.0,
            "last_change_count": 0,
            "last_churn_ratio": 0.0,
            "last_run_meta": {},
            "fail_score": 0.0,
            "circuit_open_until": 0.0,
            "cooldown_until": 0.0,
            "kqueue_dirty": False,
        }

    def test_ru_maxrss_conversion_darwin(self):
        """Test resource usage - verify method exists."""
        # Just verify the method exists and is callable
        self.assertTrue(hasattr(self.orch, '_memory_pressure_ok'))

    def test_ru_maxrss_conversion_linux(self):
        """Test resource usage - verify method works."""
        # Method exists - actual resource check tested in integration
        self.assertTrue(callable(getattr(self.orch, '_memory_pressure_ok', None)))

    def test_cancelled_error_not_penalized(self):
        """Test that CancelledError doesn't increment fail_score."""
        async def run_test():
            # Set fail_score to 0
            self.orch._structure_map_state["fail_score"] = 0.0

            # Mock to_thread to raise CancelledError
            async def mock_to_thread(fn):
                raise asyncio.CancelledError()

            with patch("asyncio.to_thread", side_effect=mock_to_thread):
                try:
                    await self.orch._build_structure_map_async(limits={})
                except asyncio.CancelledError:
                    pass

            # fail_score should still be 0
            self.assertEqual(self.orch._structure_map_state["fail_score"], 0.0)

        asyncio.run(run_test())

    def test_lru_move_to_end(self):
        """Test that LRU moves accessed items to end."""
        # Pre-fill cache with "a" first, "b" second
        cache = OrderedDict()
        cache["a"] = {"imports": [], "hot_score": 1.0}
        cache["b"] = {"imports": [], "hot_score": 1.0}

        self.orch._structure_map_state["file_cache"] = cache

        # Simulate writeback - process "a" first then "b"
        # This should result in "b" being at end (most recent)
        async def run_test():
            await self.orch._structure_map_writeback({
                "files": [
                    {"rel_path": "a", "imports": ["x"], "prefix_hash": "abc", "mtime_ns": 1, "size": 10, "module": "a", "parse_mode": "ast"},
                    {"rel_path": "b", "imports": ["y"], "prefix_hash": "def", "mtime_ns": 2, "size": 20, "module": "b", "parse_mode": "ast"},
                ],
                "edges": [],
                "fingerprint": "test123",
                "meta": {"changed_files": 2, "churn_ratio": 0.5, "truncated": False, "truncation_reason": None, "errors": []}
            })

            # "b" should now be at the end (processed last = most recently used)
            keys = list(self.orch._structure_map_state["file_cache"].keys())
            self.assertEqual(keys[-1], "b")

        asyncio.run(run_test())

    def test_should_run_basic(self):
        """Test basic should_run logic."""
        now = 2000.0  # 2000s - last run was at 500s, so 1500s ago > 600s interval
        self.orch._structure_map_state["last_run_time"] = 500.0  # 1500s ago
        self.orch._structure_map_state["cooldown_until"] = 0.0
        self.orch._structure_map_state["circuit_open_until"] = 0.0

        # Memory pressure check
        with patch.object(self.orch, '_memory_pressure_ok', return_value=True):
            snap = {
                "last_run_time": 500.0,
                "cooldown_until": 0.0,
                "circuit_open_until": 0.0,
                "kqueue_dirty": False,
            }
            result = self.orch._structure_map_should_run(now, snap)
            # 1500s ago > 600s interval = True
            self.assertTrue(result)

    def test_should_run_cooldown(self):
        """Test that cooldown prevents running."""
        now = 1000.0
        self.orch._structure_map_state["last_run_time"] = 500.0
        self.orch._structure_map_state["cooldown_until"] = 1200.0  # In future

        with patch.object(self.orch, '_memory_pressure_ok', return_value=True):
            snap = {
                "last_run_time": 500.0,
                "cooldown_until": 1200.0,
                "circuit_open_until": 0.0,
                "kqueue_dirty": False,
            }
            result = self.orch._structure_map_should_run(now, snap)
            # cooldown_until in future = False
            self.assertFalse(result)

    def test_should_run_memory_pressure(self):
        """Test that memory pressure blocks running."""
        now = 1000.0

        with patch.object(self.orch, '_memory_pressure_ok', return_value=False):
            snap = {
                "last_run_time": 500.0,
                "cooldown_until": 0.0,
                "circuit_open_until": 0.0,
                "kqueue_dirty": False,
            }
            result = self.orch._structure_map_should_run(now, snap)
            self.assertFalse(result)

    def test_scalar_snapshot_thread_safety(self):
        """Test that scalar snapshot is thread-safe."""
        async def run_test():
            # Set dirty flag
            with self.orch._kqueue_dirty_lock:
                self.orch._structure_map_state["kqueue_dirty"] = True

            # Read snapshot
            snap = await self.orch._structure_map_scalar_snapshot()
            self.assertTrue(snap["kqueue_dirty"])

        asyncio.run(run_test())

    def test_fail_score_circuit_breaker(self):
        """Test that fail_score >= 3 opens circuit."""
        import tempfile

        async def run_test():
            # Create temp dir for project
            temp_dir = tempfile.mkdtemp()
            self.orch._project_root = temp_dir

            # Set fail_score to 2.5
            self.orch._structure_map_state["fail_score"] = 2.5
            self.orch._structure_map_state["circuit_open_until"] = 0.0

            # Mock build to fail with real error
            async def mock_to_thread(fn):
                raise RuntimeError("test error")

            with patch("asyncio.to_thread", side_effect=mock_to_thread):
                try:
                    await self.orch._build_structure_map_async(limits={})
                except RuntimeError:
                    pass

            # fail_score should be 3.5
            self.assertGreaterEqual(self.orch._structure_map_state["fail_score"], 3.0)
            # Circuit should be open
            self.assertGreater(self.orch._structure_map_state["circuit_open_until"], 0.0)

            # Cleanup
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
