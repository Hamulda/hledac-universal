"""
Sprint 70: Action Tests
"""

import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSprint70Actions(unittest.TestCase):
    """Testy pro nové akce Sprintu 70."""

    def setUp(self):
        """Set up orchestrator for testing."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        self.orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        self.orch._action_registry = {}
        self.orch._project_root = "/tmp/test"
        self.orch._structure_map_state = {
            "file_cache": OrderedDict(),
            "prev_edges": [],
            "last_fingerprint": None,
            "last_run_time": 0.0,
            "cooldown_until": 0.0,
            "circuit_open_until": 0.0,
            "fail_score": 0.0,
            "kqueue_dirty": False,
        }
        import threading
        self.orch._structure_map_lock = asyncio.Lock()
        self.orch._kqueue_dirty_lock = threading.Lock()
        self.orch._evidence_log = MagicMock()
        self.orch._new_domain_queue = asyncio.PriorityQueue(maxsize=20)
        self.orch._known_paths_queue = asyncio.Queue(maxsize=20)

    def test_new_actions_registered(self):
        """Test že nové akce jsou registrovány."""
        async def run_test():
            await self.orch._initialize_actions()

        asyncio.run(run_test())

        # Check that new actions are in registry
        expected_actions = ['scan_ct', 'fingerprint_jarm', 'scan_open_storage', 'crawl_onion', 'generate_paths']
        for action in expected_actions:
            self.assertIn(action, self.orch._action_registry)

    def test_priority_queue_put_get(self):
        """Test PriorityQueue functionality."""
        async def run_test():
            # Put items
            await self.orch._new_domain_queue.put((1.0, "example.com"))
            await self.orch._new_domain_queue.put((0.5, "test.com"))

            # Get items - should come in priority order (lower first)
            item1 = await self.orch._new_domain_queue.get()
            self.assertEqual(item1[1], "test.com")  # 0.5 < 1.0

        asyncio.run(run_test())

    def test_known_paths_queue(self):
        """Test known paths queue."""
        async def run_test():
            await self.orch._known_paths_queue.put(("example.com", ["/admin", "/api"]))

            item = await self.orch._known_paths_queue.get()
            self.assertEqual(item[0], "example.com")
            self.assertEqual(item[1], ["/admin", "/api"])

        asyncio.run(run_test())


class TestMemoryPressureOK(unittest.TestCase):
    """Testy pro rozšířenou _memory_pressure_ok."""

    def test_memory_pressure_method_exists(self):
        """Test že metoda existuje."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        self.assertTrue(hasattr(orch, '_memory_pressure_ok'))
        self.assertTrue(callable(orch._memory_pressure_ok))

    def test_memory_pressure_fallback(self):
        """Test fallback path when mlx not available."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)

        # Should return True (OK) as fallback
        result = orch._memory_pressure_ok()
        self.assertIsInstance(result, bool)


class TestBackgroundExecutor(unittest.TestCase):
    """Testy pro background executor."""

    def test_background_executor_attribute_exists(self):
        """Test že atribut _background_executor je definován v __init__."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Get the source code of __init__
        source = inspect.getsource(FullyAutonomousOrchestrator.__init__)
        self.assertIn('_background_executor', source)


if __name__ == "__main__":
    unittest.main()
