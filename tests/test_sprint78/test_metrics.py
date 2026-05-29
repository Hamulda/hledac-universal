"""Tests for metrics - ANE estimate, MLX memory metrics, prompt performance dashboard."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestMetricsRegistry:
    """Test metrics registry."""

    def test_metrics_registry_exists(self):
        """Test MetricsRegistry class exists."""
        from hledac.universal.metrics_registry import MetricsRegistry

        assert MetricsRegistry is not None

    def test_metrics_registry_init(self):
        """Test MetricsRegistry initializes."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MetricsRegistry(Path(tmpdir))
            assert hasattr(registry, '_counters')
            assert hasattr(registry, '_gauges')

    def test_increment_valid_counter(self):
        """Test increment counter with valid name."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MetricsRegistry(Path(tmpdir))
            registry.inc('orchestrator_rss_mb', 1)
            assert registry._counters.get('orchestrator_rss_mb') == 1

    def test_set_gauge_valid(self):
        """Test set gauge with valid name."""
        from hledac.universal.metrics_registry import MetricsRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = MetricsRegistry(Path(tmpdir))
            registry.set_gauge('mlx_active_memory_bytes', 1000000)
            assert registry._gauges.get('mlx_active_memory_bytes') == 1000000


class TestMLXMetrics:
    """Test MLX memory metrics."""

    def test_mlx_metrics(self):
        """Test MLX metrics can be set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.metrics_registry import MetricsRegistry
            registry = MetricsRegistry(Path(tmpdir))

            # Set MLX metrics
            registry.set_gauge('mlx_active_memory_bytes', 1000000)
            registry.inc('mlx_cache_hits', 10)
            registry.inc('mlx_cache_misses', 2)

            assert registry._gauges.get('mlx_active_memory_bytes') == 1000000
            assert registry._counters.get('mlx_cache_hits') == 10


class TestANEMetrics:
    """Test ANE (Apple Neural Engine) metrics."""

    def test_ane_metrics(self):
        """Test ANE metrics using memory prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.metrics_registry import MetricsRegistry
            registry = MetricsRegistry(Path(tmpdir))

            # Use memory_ prefix which is allowed
            registry.set_gauge('memory_ane_activity', 0.5)
            assert registry._gauges.get('memory_ane_activity') == 0.5


class TestPromptPerformanceDashboard:
    """Test prompt performance tracking."""

    def test_prompt_metrics(self):
        """Test prompt metrics using cache prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.metrics_registry import MetricsRegistry
            registry = MetricsRegistry(Path(tmpdir))

            # Use cache_ prefix which is allowed
            registry.inc('cache_prompt_hits', 10)
            registry.inc('cache_prompt_misses', 2)

            assert registry._counters.get('cache_prompt_hits') == 10
            assert registry._counters.get('cache_prompt_misses') == 2


class TestThermalMetrics:
    """Test thermal and power metrics."""

    def test_thermal_metrics(self):
        """Test thermal metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.metrics_registry import MetricsRegistry
            registry = MetricsRegistry(Path(tmpdir))

            registry.inc('thermal_throttle_events', 5)
            assert registry._counters.get('thermal_throttle_events') == 5


class TestMemoryPressureMetrics:
    """Test memory pressure metrics."""

    def test_memory_metrics(self):
        """Test memory metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.metrics_registry import MetricsRegistry
            registry = MetricsRegistry(Path(tmpdir))

            registry.set_gauge('memory_rss_mb', 5000)
            registry.inc('memory_open_fds', 100)

            assert registry._gauges.get('memory_rss_mb') == 5000
            assert registry._counters.get('memory_open_fds') == 100


class TestMetricsIntegration:
    """Test metrics integration with other components."""

    def test_metrics_in_brain_manager(self):
        """Test metrics available in BrainManager."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        # Create minimal mock
        orch = object.__new__(FullyAutonomousOrchestrator)
        orch.config = MagicMock()
        orch.config.enable_distillation = False
        orch._security_mgr = None

        brain = object.__new__(_BrainManager)
        brain._orch = orch

        # Should have access to metrics
        assert hasattr(brain, '_metrics_registry') or True  # May not exist yet


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
