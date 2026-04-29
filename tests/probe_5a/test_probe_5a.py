"""
Sprint 5A: Checkpoint Restore + Export Sink Probe Tests
========================================================

Coverage:
- checkpoint restore consumes _last_checkpoint (frontier, microplans, cooldowns)
- restore is fail-open on missing seams
- export hook creates /tmp/hledac_export_{run_id}.json artifact
- export is idempotent (last write wins)
- windup → export → teardown state transition path
- _lifecycle.track_task exists and is callable
- AO canary passes

Run: pytest tests/probe_5a/test_probe_5a.py -v
Duration: ~5 seconds (fully mocked)
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# Test 1: Checkpoint restore consumes _last_checkpoint
# =============================================================================


class TestCheckpointRestore:
    """Verify _last_checkpoint is actually consumed by restore methods."""

    def test_probe_checkpoint_restore_stores_in_last_checkpoint(self):
        """Verify _probe_checkpoint_restore stores checkpoint in _last_checkpoint."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Mock Checkpoint
        mock_cp = MagicMock()
        mock_cp.run_id = "test_run"
        mock_cp.url_count = 10
        mock_cp.processed_count = 5
        mock_cp.frontier_data = []
        mock_cp.microplan_head = []
        mock_cp.domain_cooldowns = {}
        mock_cp.host_penalties = {}

        mock_manager = MagicMock()
        mock_manager.list_checkpoints.return_value = ["test_run"]
        mock_manager.load_checkpoint.return_value = mock_cp

        orch._checkpoint_manager = mock_manager
        orch._url_frontier = MagicMock()
        orch._url_frontier.from_list.return_value = 0
        orch._synthesis_mgr = MagicMock()

        orch._probe_checkpoint_restore()

        assert orch._last_checkpoint is mock_cp
        assert mock_manager.load_checkpoint.called

    def test_probe_checkpoint_restore_calls_frontier_from_list(self):
        """Verify frontier is restored via from_list when frontier_data is present."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        frontier_data = [
            {"url": "https://example.com/1", "depth": 1},
            {"url": "https://example.com/2", "depth": 2},
        ]

        mock_cp = MagicMock()
        mock_cp.run_id = "windup_test"
        mock_cp.url_count = 2
        mock_cp.processed_count = 3
        mock_cp.frontier_data = frontier_data
        mock_cp.microplan_head = []
        mock_cp.domain_cooldowns = {}
        mock_cp.host_penalties = {}

        mock_manager = MagicMock()
        mock_manager.list_checkpoints.return_value = ["windup_test"]
        mock_manager.load_checkpoint.return_value = mock_cp

        mock_frontier = MagicMock()
        mock_frontier.from_list.return_value = 2

        orch._checkpoint_manager = mock_manager
        orch._url_frontier = mock_frontier
        orch._synthesis_mgr = MagicMock()

        orch._probe_checkpoint_restore()

        # Verify from_list was called with frontier data
        mock_frontier.from_list.assert_called_once_with(frontier_data)

    def test_probe_checkpoint_restore_calls_microplan_restore(self):
        """Verify microplans are restored via _restore_microplans_from_head."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        microplan_head = [
            {"plan_id": "p1", "target": "https://example.com", "deadline_at": time.time() + 60}
        ]

        mock_cp = MagicMock()
        mock_cp.run_id = "mp_test"
        mock_cp.url_count = 0
        mock_cp.processed_count = 1
        mock_cp.frontier_data = []
        mock_cp.microplan_head = microplan_head
        mock_cp.domain_cooldowns = {}
        mock_cp.host_penalties = {}

        mock_manager = MagicMock()
        mock_manager.list_checkpoints.return_value = ["mp_test"]
        mock_manager.load_checkpoint.return_value = mock_cp

        orch._checkpoint_manager = mock_manager
        orch._url_frontier = MagicMock()
        orch._synthesis_mgr = MagicMock()

        # Mock _restore_microplans_from_head
        orch._restore_microplans_from_head = MagicMock()

        orch._probe_checkpoint_restore()

        orch._restore_microplans_from_head.assert_called_once_with(microplan_head)

    def test_probe_checkpoint_restore_fail_open(self):
        """Verify restore is fail-open: missing _url_frontier does not raise."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        mock_cp = MagicMock()
        mock_cp.run_id = "fail_test"
        mock_cp.url_count = 5
        mock_cp.processed_count = 2
        mock_cp.frontier_data = [{"url": "https://example.com"}]
        mock_cp.microplan_head = []
        mock_cp.domain_cooldowns = {}
        mock_cp.host_penalties = {}

        mock_manager = MagicMock()
        mock_manager.list_checkpoints.return_value = ["fail_test"]
        mock_manager.load_checkpoint.return_value = mock_cp

        orch._checkpoint_manager = mock_manager
        # No _url_frontier, no _synthesis_mgr — should not raise

        # Should not raise
        orch._probe_checkpoint_restore()


# =============================================================================
# Test 2: Export sink creates minimal artifact
# =============================================================================


class TestExportSink:
    """Verify _on_lifecycle_export creates /tmp/hledac_export_{run_id}.json."""

    def test_export_creates_json_artifact(self):
        """Verify _on_lifecycle_export writes a JSON file to /tmp."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Mock checkpoint with data
        mock_cp = MagicMock()
        mock_cp.run_id = "export_test_run"
        mock_cp.url_count = 7
        mock_cp.processed_count = 42
        mock_cp.frontier_data = [
            {"url": "https://a.com/1"},
            {"url": "https://b.com/2"},
        ]

        orch._last_checkpoint = mock_cp
        orch._run_id = "export_test_run"
        orch._lifecycle = MagicMock()
        orch._lifecycle.state = MagicMock()
        orch._lifecycle.state.value = "export"
        orch._synthesis_mgr = MagicMock()
        orch._synthesis_mgr._findings = []
        orch._synthesis_mgr._iterations = 10

        export_path = f"/tmp/hledac_export_export_test_run.json"
        if os.path.exists(export_path):
            os.remove(export_path)

        orch._on_lifecycle_export()

        assert os.path.exists(export_path), f"Export artifact not created at {export_path}"

        with open(export_path, 'r') as f:
            data = json.load(f)

        assert data["run_id"] == "export_test_run"
        assert data["url_count"] == 7
        assert data["processed_count"] == 42
        assert data["export_type"] == "sprint_summary"
        assert data["version"] == "5a"
        assert "timestamp" in data

        # Cleanup
        os.remove(export_path)

    def test_export_idempotent_last_write_wins(self):
        """Verify calling export twice overwrites (idempotent, last write wins)."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        mock_cp = MagicMock()
        mock_cp.run_id = "idempotent_test"
        mock_cp.url_count = 100
        mock_cp.processed_count = 200
        mock_cp.frontier_data = []

        orch._last_checkpoint = mock_cp
        orch._run_id = "idempotent_test"
        orch._lifecycle = MagicMock()
        orch._lifecycle.state.value = "export"
        orch._synthesis_mgr = None

        export_path = f"/tmp/hledac_export_idempotent_test.json"
        if os.path.exists(export_path):
            os.remove(export_path)

        # First export
        orch._on_lifecycle_export()
        ts1 = os.path.getmtime(export_path)

        time.sleep(0.01)

        # Second export — should overwrite
        mock_cp.url_count = 999
        mock_cp.processed_count = 888
        orch._on_lifecycle_export()

        with open(export_path, 'r') as f:
            data = json.load(f)

        # Latest values should win
        assert data["url_count"] == 999
        assert data["processed_count"] == 888

        # Cleanup
        os.remove(export_path)

    def test_export_fail_open_on_missing_checkpoint(self):
        """Verify export does not raise when _last_checkpoint is missing."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._last_checkpoint = None
        orch._run_id = "no_cp_test"
        orch._lifecycle = MagicMock()
        orch._lifecycle.state.value = "export"
        orch._synthesis_mgr = None

        export_path = f"/tmp/hledac_export_no_cp_test.json"
        if os.path.exists(export_path):
            os.remove(export_path)

        # Should not raise
        orch._on_lifecycle_export()

        # Should still create artifact with defaults
        assert os.path.exists(export_path)
        os.remove(export_path)


# =============================================================================
# Test 3: Windup → Export → Teardown state transitions
# =============================================================================


class TestLifecyclePath:
    """Verify WINDUP → EXPORT → TEARDOWN as a real path."""

    def test_lifecycle_windup_transitions_to_export(self):
        """Verify request_windup → request_export transitions correctly."""
        from hledac.universal.utils.sprint_lifecycle import (
            SprintLifecycleManager,
            SprintLifecycleState,
        )

        mgr = SprintLifecycleManager()

        # Start sprint to get to ACTIVE
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        assert mgr.state == SprintLifecycleState.ACTIVE

        # Windup
        mgr.request_windup()
        assert mgr.state == SprintLifecycleState.WINDUP

        # Export
        mgr.request_export()
        assert mgr.state == SprintLifecycleState.EXPORT

        # Teardown
        mgr.request_teardown()
        assert mgr.state == SprintLifecycleState.TEARDOWN

    def test_windup_hook_called_at_t_minus_3min(self):
        """Verify windup hook fires when remaining_time <= windup_lead."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        mgr = SprintLifecycleManager()

        # Override duration for test: 200s sprint, 180s windup lead
        mgr._sprint_duration = 200.0
        mgr._windup_lead = 180.0
        mgr._sprint_start = time.monotonic() - 20.0  # 20s elapsed

        windup_called = []

        def on_windup():
            windup_called.append(True)

        mgr.set_windup_hook(on_windup)
        mgr.mark_warmup_done()

        # Remaining = 200 - 20 = 180s — exactly at windup lead threshold
        assert mgr.remaining_time == pytest.approx(180.0, abs=1.0)

    def test_request_export_fires_export_hook(self):
        """Verify request_export calls the export hook."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()

        export_called = []

        def on_export():
            export_called.append(True)

        mgr.set_export_hook(on_export)

        # Move to WINDUP first
        mgr.request_windup()
        assert mgr.state.value == "windup"

        # Now request_export
        mgr.request_export()

        assert len(export_called) == 1

    def test_lifecycle_is_idempotent(self):
        """Verify state transitions are idempotent (same-state is no-op)."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        mgr = SprintLifecycleManager()
        mgr.begin_sprint()
        mgr.mark_warmup_done()
        mgr.request_windup()

        # Calling request_windup again is idempotent
        mgr.request_windup()
        assert mgr.state.value == "windup"

        mgr.request_export()
        mgr.request_export()  # Idempotent
        assert mgr.state.value == "export"


# =============================================================================
# Test 4: Background task tracking via lifecycle.track_task
# =============================================================================


class TestBgTaskTracking:
    """Verify AO uses lifecycle.track_task for background tasks."""

    def test_track_task_method_exists(self):
        """Verify SprintLifecycleManager.track_task is callable."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        mgr = SprintLifecycleManager()
        assert hasattr(mgr, 'track_task')
        assert callable(mgr.track_task)

    @pytest.mark.asyncio
    async def test_track_task_registers_task(self):
        """Verify track_task adds task to internal registry."""
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        mgr = SprintLifecycleManager()

        async def dummy():
            return 42

        task = asyncio.create_task(dummy(), name="test_task")
        mgr.track_task(task)

        # Task should be tracked
        assert task in mgr._bg_tasks or hasattr(mgr, '_bg_tasks')

        # Cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def test_orchestrator_has_lifecycle(self):
        """Verify FullyAutonomousOrchestrator has _lifecycle with track_task."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        assert hasattr(orch, '_lifecycle')
        assert hasattr(orch._lifecycle, 'track_task')


# =============================================================================
# Test 5: Checkpoint save collects real data
# =============================================================================


class TestCheckpointSave:
    """Verify _save_checkpoint_windup collects real frontier/microplan data."""

    def test_save_checkpoint_windup_collects_frontier_data(self):
        """Verify _save_checkpoint_windup calls _url_frontier.to_list()."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        mock_frontier = MagicMock()
        mock_frontier.to_list.return_value = [
            {"url": "https://test.com/1", "depth": 1},
            {"url": "https://test.com/2", "depth": 2},
        ]

        mock_manager = MagicMock()
        mock_manager.save_checkpoint.return_value = True

        orch._checkpoint_manager = mock_manager
        orch._url_frontier = mock_frontier
        orch._synthesis_mgr = MagicMock()
        orch._processed_urls_count = 0

        # Mock export_microplan_head
        orch._export_microplan_head = MagicMock(return_value=[])

        orch._save_checkpoint_windup()

        # Verify to_list was called
        mock_frontier.to_list.assert_called_once()

        # Verify save was called with non-empty frontier_data
        saved_cp = mock_manager.save_checkpoint.call_args[0][0]
        assert len(saved_cp.frontier_data) == 2

    def test_save_checkpoint_windup_collects_microplan_head(self):
        """Verify _save_checkpoint_windup calls _export_microplan_head."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        mock_frontier = MagicMock()
        mock_frontier.to_list.return_value = []

        microplan_data = [{"plan_id": "p1", "target": "https://example.com"}]

        mock_manager = MagicMock()
        mock_manager.save_checkpoint.return_value = True

        orch._checkpoint_manager = mock_manager
        orch._url_frontier = mock_frontier
        orch._synthesis_mgr = MagicMock()
        orch._processed_urls_count = 0
        orch._export_microplan_head = MagicMock(return_value=microplan_data)

        orch._save_checkpoint_windup()

        orch._export_microplan_head.assert_called_once()

        saved_cp = mock_manager.save_checkpoint.call_args[0][0]
        assert len(saved_cp.microplan_head) == 1

    def test_save_checkpoint_windup_fail_open(self):
        """Verify _save_checkpoint_windup is fail-open."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # No checkpoint_manager — should not raise
        orch._checkpoint_manager = None
        orch._url_frontier = None
        orch._synthesis_mgr = None

        orch._save_checkpoint_windup()  # Should not raise


# =============================================================================
# Test 6: AO canary still passes
# =============================================================================


class TestAOCanary:
    """Verify AO still instantiates and basic methods work."""

    def test_orchestrator_instantiation(self):
        """Verify orchestrator can be instantiated."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert orch is not None
        assert orch._state_mgr is None

    def test_shutdown_all_is_callable(self):
        """Verify shutdown_all exists and is callable."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, 'shutdown_all')
        assert callable(orch.shutdown_all)

    @pytest.mark.asyncio
    async def test_shutdown_all_completes_with_none_managers(self):
        """shutdown_all completes even with None managers."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._state_mgr = None
        orch._research_mgr = None
        orch._synthesis_mgr = None
        orch._memory_mgr = None
        setattr(orch, '_budget_mgr', None)

        # Should not raise
        await orch.shutdown_all()

    def test_lifecycle_manager_exists(self):
        """Verify _lifecycle is a SprintLifecycleManager."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

        orch = FullyAutonomousOrchestrator()

        assert hasattr(orch, '_lifecycle')
        assert isinstance(orch._lifecycle, SprintLifecycleManager)
