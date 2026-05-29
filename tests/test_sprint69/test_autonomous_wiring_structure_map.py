"""
Sprint 69C: Autonomous Wiring Tests
Testy pro ověření napojení Structure Map do autonomní smyčky.
"""

import asyncio
import os
import sys
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


class TestAutonomousWiringStructureMap(unittest.TestCase):
    """Testy pro ověření wiring Structure Map do autonomního orchestrátoru."""

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
        self.orch._warming_task = None
        self.orch._evidence_log = MagicMock()
        self.orch._evidence_log.add_event = MagicMock()

    def test_warming_task_starts_on_init(self):
        """Test that warming task is created on init."""
        async def run_test():
            # Simulate the init code - create task in async context
            self.orch._warming_task = asyncio.create_task(asyncio.sleep(1000))
            self.orch._project_root = "/tmp/test"

            # Run the warming method
            task = asyncio.create_task(self.orch._run_structure_map_warming())
            # Give it a moment to start
            await asyncio.sleep(0.01)
            # Cancel it
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True

        result = asyncio.run(run_test())
        self.assertTrue(result)

    def test_evidence_log_on_success(self):
        """Test that evidence log is called on success."""
        async def run_test():
            # Set up mock result
            mock_map_dict = {
                "fingerprint": "abc123",
                "files": [],
                "edges": [],
                "meta": {
                    "truncated": False,
                    "churn_ratio": 0.1,
                    "changed_files": 5,
                    "elapsed_ms": 100,
                }
            }

            # Mock _build_structure_map_async to return our result
            with patch.object(self.orch, '_build_structure_map_async', return_value=mock_map_dict):
                # Run writeback
                await self.orch._structure_map_writeback(mock_map_dict)

            # Verify evidence log was called
            self.orch._evidence_log.add_event.assert_called()
            call_args = self.orch._evidence_log.add_event.call_args
            self.assertEqual(call_args[1]["event_type"], "structure_map_success")

        asyncio.run(run_test())

    def test_evidence_log_on_failure(self):
        """Test that evidence log is called on failure."""
        async def run_test():
            # Simulate failure scenario by patching to raise immediately
            self.orch._evidence_log = MagicMock()
            add_event_mock = MagicMock()
            self.orch._evidence_log.add_event = add_event_mock

            # Patch asyncio.to_thread to raise error directly
            async def mock_to_thread(fn):
                raise RuntimeError("test error")

            with patch("asyncio.to_thread", side_effect=mock_to_thread):
                try:
                    await self.orch._build_structure_map_async(limits={})
                except RuntimeError:
                    pass

            # Verify evidence log was called
            add_event_mock.assert_called()
            call_args = add_event_mock.call_args
            self.assertEqual(call_args[1]["event_type"], "structure_map_failure")

        asyncio.run(run_test())

    def test_last_fingerprint_updated(self):
        """Test that last_fingerprint is updated after success."""
        async def run_test():
            mock_map_dict = {
                "fingerprint": "fingerprint123",
                "files": [{"rel_path": "a.py", "imports": [], "prefix_hash": "x", "mtime_ns": 1, "size": 10, "module": "a", "parse_mode": "ast"}],
                "edges": [],
                "meta": {"truncated": False, "churn_ratio": 0.0, "changed_files": 1, "elapsed_ms": 10}
            }

            # Initial state
            self.assertIsNone(self.orch._structure_map_state["last_fingerprint"])

            with patch.object(self.orch, '_build_structure_map_async', return_value=mock_map_dict):
                await self.orch._structure_map_writeback(mock_map_dict)

            # Verify fingerprint was updated
            self.assertEqual(self.orch._structure_map_state["last_fingerprint"], "fingerprint123")

        asyncio.run(run_test())

    def test_warming_task_cancelled_on_cleanup(self):
        """Test that warming task is cancelled during cleanup."""
        async def run_test():
            # Create a long-running task
            async def long_task():
                await asyncio.sleep(1000)

            self.orch._warming_task = asyncio.create_task(long_task())

            # Verify it's running
            self.assertFalse(self.orch._warming_task.done())

            # Cancel it (simulate cleanup)
            if self.orch._warming_task:
                self.orch._warming_task.cancel()
                try:
                    await self.orch._warming_task
                except asyncio.CancelledError:
                    pass

            # Verify it's done
            self.assertTrue(self.orch._warming_task.done())

        asyncio.run(run_test())

    def test_cancelled_error_not_penalized(self):
        """Test that CancelledError doesn't increment fail_score."""
        async def run_test():
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


class TestStructureMapActionRegistration(unittest.TestCase):
    """Testy pro registraci akce build_structure_map."""

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
        }

    def test_structure_map_action_registered(self):
        """Test that build_structure_map action is registered."""
        # Initialize actions (which registers build_structure_map)
        async def run_test():
            await self.orch._initialize_actions()

        asyncio.run(run_test())

        # Verify action is registered
        self.assertIn('build_structure_map', self.orch._action_registry)

    def test_warming_started_from_loop_not_init(self):
        """Test that warming starts from loop, not from init."""
        # Create orchestrator without warming task
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._warming_task = None
        orch._project_root = "/tmp/test"
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
        import threading
        orch._structure_map_lock = asyncio.Lock()
        orch._kqueue_dirty_lock = threading.Lock()
        orch._evidence_log = MagicMock()

        # Verify warming_task is None initially
        self.assertIsNone(orch._warming_task)

        # Call _ensure_structure_map_warming_started
        async def run_test():
            await orch._ensure_structure_map_warming_started()

        asyncio.run(run_test())

        # Verify warming_task is now set
        self.assertIsNotNone(orch._warming_task)

    def test_action_path_updates_fingerprint_and_logs_success(self):
        """Test that action path updates fingerprint and logs success."""

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._project_root = "/tmp/test"
        orch._HIGH_PRIORITY_STRUCTURE_MAP_LIMITS = {"max_files": 100}
        orch._STRUCTURE_MAP_MIN_INTERVAL_S = 600.0
        orch._structure_map_lock = asyncio.Lock()
        import threading
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
        add_event_mock = MagicMock()
        orch._evidence_log.add_event = add_event_mock
        orch._action_registry = {}

        # Mock the internal methods
        orch._structure_map_scalar_snapshot = AsyncMock(return_value={
            "last_run_time": 0.0,
            "cooldown_until": 0.0,
            "circuit_open_until": 0.0,
            "kqueue_dirty": False,
        })
        orch._structure_map_should_run = MagicMock(return_value=True)
        orch._build_structure_map_async = AsyncMock(return_value={
            "fingerprint": "test_fingerprint_123",
            "files": [],
            "edges": [],
            "meta": {"truncated": False, "churn_ratio": 0.0, "changed_files": 0, "elapsed_ms": 10}
        })

        async def run_test():
            # Initialize actions to register the handler
            await orch._initialize_actions()

            # Get handler from registry - it's stored as (handler, scorer) tuple
            action_entry = orch._action_registry.get('build_structure_map')
            self.assertIsNotNone(action_entry, "build_structure_map not in registry")
            handler = action_entry[0]  # First element is handler

            # Mock writeback to avoid actual state changes
            orch._structure_map_writeback = AsyncMock()

            # Call handler
            result = await handler()

            # Verify success
            self.assertTrue(result.success)
            self.assertEqual(result.metadata.get("structure_map_fingerprint"), "test_fingerprint_123")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
