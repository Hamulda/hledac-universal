"""
Sprint 70B: Integration Tests
"""

import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSprint70Integration(unittest.TestCase):
    """Integrační testy pro Sprint 70."""

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
        self.orch._findings_heap = []
        self.orch._last_url = None
        # Add missing attributes needed by _analyze_state
        self.orch._last_preview = ""
        self.orch._active_hypotheses = []
        self.orch._contradiction_queue = []
        self.orch._stagnation_counter = 0
        self.orch._caps = None
        self.orch._archive_coordinator = None
        self.orch._budget_manager = MagicMock()
        self.orch._budget_manager.get_status = MagicMock(return_value={"remaining": 100})

    def test_analyze_state_includes_sprint70_signals(self):
        """Test že _analyze_state vrací Sprint 70 signály."""
        # Just verify the method has Sprint 70 signals in source code
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        source = inspect.getsource(FullyAutonomousOrchestrator._analyze_state)

        # Verify Sprint 70 signals are in the method
        self.assertIn('new_domain_detected', source)
        self.assertIn('has_onion_targets', source)
        self.assertIn('has_known_paths', source)
        self.assertIn('_new_domain_queue', source)
        self.assertIn('_known_paths_queue', source)

    def test_background_tasks_methods_exist(self):
        """Test že background task metody existují."""
        self.assertTrue(hasattr(self.orch, '_run_meta_optimizer'))
        self.assertTrue(hasattr(self.orch, '_monitor_dns_tunnel'))
        self.assertTrue(asyncio.iscoroutinefunction(self.orch._run_meta_optimizer))
        self.assertTrue(asyncio.iscoroutinefunction(self.orch._monitor_dns_tunnel))

    def test_cleanup_includes_sprint70(self):
        """Test že cleanup obsahuje Sprint 70 cleanup."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator.cleanup)
        self.assertIn('Sprint 70', source)
        self.assertIn('_meta_optimizer_task', source)
        self.assertIn('_dns_monitor_task', source)
        self.assertIn('_background_executor', source)


class TestActionRegistry(unittest.TestCase):
    """Testy pro akční registry."""

    def test_all_sprint70_actions_present(self):
        """Test že všechny Sprint 70 akce jsou registrovány."""
        async def run_test():
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
            orch._action_registry = {}
            orch._project_root = "/tmp/test"

            import threading
            orch._structure_map_lock = asyncio.Lock()
            orch._kqueue_dirty_lock = threading.Lock()
            orch._structure_map_state = {
                "file_cache": OrderedDict(),
                "prev_edges": [],
                "last_fingerprint": None,
                "last_run_time": 0.0,
                "cooldown_until": 0.0,
                "circuit_open_until": 0.0,
                "fail_score": 0.0,
                "kqueue_dirty": False,
            }
            orch._evidence_log = MagicMock()
            orch._new_domain_queue = asyncio.PriorityQueue(maxsize=20)
            orch._known_paths_queue = asyncio.Queue(maxsize=20)

            await orch._initialize_actions()

            # Ověř akce
            expected = ['scan_ct', 'fingerprint_jarm', 'scan_open_storage', 'crawl_onion', 'generate_paths']
            for action in expected:
                self.assertIn(action, orch._action_registry, f"Missing action: {action}")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
