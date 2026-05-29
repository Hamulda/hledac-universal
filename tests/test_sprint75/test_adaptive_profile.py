"""
Tests for adaptive inference profile (Sprint 75).
"""

from unittest.mock import MagicMock

import pytest


class TestAdaptiveProfile:
    """Test adaptive inference profile."""

    def test_profile_attributes_exist(self):
        """Test profile attributes exist in _BrainManager."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        # Create mock orchestrator
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None

        manager = _BrainManager(orch)
        assert hasattr(manager, '_profile')
        assert hasattr(manager, '_profile_task')
        assert hasattr(manager, '_stop_profile')
        assert hasattr(manager, '_profile_lock')
        assert hasattr(manager, '_coreml_classifier')

    def test_profile_defaults_to_full(self):
        """Test default profile is 'full'."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None

        manager = _BrainManager(orch)
        assert manager._profile == "full"

    def test_start_profile_manager_exists(self):
        """Test start_profile_manager method exists."""
        from hledac.universal.autonomous_orchestrator import _BrainManager

        assert hasattr(_BrainManager, 'start_profile_manager')

    def test_profile_manager_exists(self):
        """Test _profile_manager method exists."""
        from hledac.universal.autonomous_orchestrator import _BrainManager

        assert hasattr(_BrainManager, '_profile_manager')

    def test_apply_profile_exists(self):
        """Test _apply_profile method exists."""
        from hledac.universal.autonomous_orchestrator import _BrainManager

        assert hasattr(_BrainManager, '_apply_profile')

    def test_stop_profile_manager_exists(self):
        """Test stop_profile_manager method exists."""
        from hledac.universal.autonomous_orchestrator import _BrainManager

        assert hasattr(_BrainManager, 'stop_profile_manager')


class TestProfileTransitions:
    """Test profile transition logic."""

    @pytest.mark.asyncio
    async def test_apply_profile_no_draft(self):
        """Test profile transition to no-draft."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None

        manager = _BrainManager(orch)

        # Create mock hermes
        manager.hermes = MagicMock()
        manager.hermes._speculative_enabled = True
        manager.hermes._draft_model_obj = MagicMock()

        manager._profile = "no-draft"
        await manager._apply_profile()

        assert manager.hermes._speculative_enabled is False

    @pytest.mark.asyncio
    async def test_apply_profile_short_context(self):
        """Test profile transition to short-context."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None

        manager = _BrainManager(orch)

        # Create mock hermes
        manager.hermes = MagicMock()
        manager.hermes._speculative_enabled = True
        manager.hermes._draft_model_obj = MagicMock()
        manager.hermes._max_context_tokens = 8192

        manager._profile = "short-context"
        await manager._apply_profile()

        assert manager.hermes._speculative_enabled is False
        assert manager.hermes._max_context_tokens == 2048

    @pytest.mark.asyncio
    async def test_apply_profile_full(self):
        """Test profile transition to full."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None

        manager = _BrainManager(orch)

        # Create mock hermes
        manager.hermes = MagicMock()
        manager.hermes._speculative_enabled = False

        manager._profile = "full"
        await manager._apply_profile()

        assert manager.hermes._speculative_enabled is True


class TestMLXMemoryUsage:
    """Test MLX memory usage helper."""

    def test_get_mlx_memory_usage_exists(self):
        """Test _get_mlx_memory_usage method exists."""
        from hledac.universal.autonomous_orchestrator import _BrainManager

        assert hasattr(_BrainManager, '_get_mlx_memory_usage')

    def test_get_mlx_memory_usage_returns_dict(self):
        """Test _get_mlx_memory_usage returns dict."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None

        manager = _BrainManager(orch)
        result = manager._get_mlx_memory_usage()

        assert isinstance(result, dict)
        assert 'active_mb' in result
        assert 'peak_mb' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
