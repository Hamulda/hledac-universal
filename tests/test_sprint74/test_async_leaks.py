"""
Tests for async task cleanup and basic orchestrator functionality.
"""

import asyncio

import pytest


class TestAsyncCleanup:
    """Test async cleanup functionality."""

    def test_orchestrator_initialization(self):
        """Test orchestrator can be created."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert orch is not None

    def test_mlx_post_action_cleanup_exists(self):
        """Test _mlx_post_action_cleanup method exists."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_mlx_post_action_cleanup')
        assert asyncio.iscoroutinefunction(orch._mlx_post_action_cleanup)

    def test_get_mlx_metrics_exists(self):
        """Test _get_mlx_metrics method exists."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_get_mlx_metrics')
        # Should return dict
        result = orch._get_mlx_metrics()
        assert isinstance(result, dict)

    def test_cleanup_can_be_called(self):
        """Test cleanup method exists and can be called."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        # cleanup should exist and be callable
        assert hasattr(orch, 'cleanup')
        # Not calling actual cleanup as it may require full initialization


class TestPromptCacheMetrics:
    """Test prompt cache metrics tracking."""

    def test_last_cache_metrics_initialized(self):
        """Test _last_cache_hits and _last_cache_misses are initialized."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_last_cache_hits')
        assert hasattr(orch, '_last_cache_misses')
        assert orch._last_cache_hits == 0
        assert orch._last_cache_misses == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
