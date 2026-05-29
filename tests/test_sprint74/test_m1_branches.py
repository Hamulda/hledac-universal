"""
Tests for M1-specific branches and hardware features.
"""

import sys
import time

import pytest

# Skip all tests if not Darwin
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


class TestM1Branches:
    """Test M1-specific code branches."""

    def test_dynamic_metal_limit_function_exists(self):
        """Test _get_dynamic_metal_limit function exists."""
        from hledac.universal import autonomous_orchestrator
        assert hasattr(autonomous_orchestrator, '_get_dynamic_metal_limit')

    @darwin_only
    def test_darwin_sysctl_returns_positive(self):
        """Test that _get_dynamic_metal_limit returns positive value."""
        from hledac.universal.autonomous_orchestrator import _get_dynamic_metal_limit
        limit = _get_dynamic_metal_limit()
        assert limit > 0

    def test_circuit_breaker_logic(self):
        """Test structure map circuit breaker."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # Set circuit breaker to open
        orch._structure_map_state["fail_score"] = 3.0
        orch._structure_map_state["circuit_open_until"] = time.time() + 3600

        snap = {
            "last_run_time": 0,
            "cooldown_until": 0,
            "circuit_open_until": orch._structure_map_state["circuit_open_until"],
            "kqueue_dirty": False
        }

        # Should NOT run when circuit is open
        assert not orch._structure_map_should_run(time.time(), snap)

        # Should run when circuit is closed
        orch._structure_map_state["circuit_open_until"] = 0
        snap["circuit_open_until"] = 0
        assert orch._structure_map_should_run(time.time(), snap)

    def test_thermal_state_enum(self):
        """Test ThermalState enum values."""
        from hledac.universal.autonomous_orchestrator import ThermalState

        assert ThermalState.NORMAL == 0
        assert ThermalState.WARM == 1
        assert ThermalState.HOT == 2
        assert ThermalState.CRITICAL == 3

    def test_battery_detection(self):
        """Test battery detection returns bool."""
        from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator()
        on_battery = coord._on_battery_power()
        assert isinstance(on_battery, bool)

    def test_thermal_trend_detection(self):
        """Test thermal trend from history."""
        from hledac.universal.coordinators.memory_coordinator import ThermalState, UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator()
        coord._thermal_history = [
            (time.time() - 60, ThermalState.NORMAL),
            (time.time() - 30, ThermalState.WARM),
            (time.time(), ThermalState.HOT),
        ]

        trend = coord.get_thermal_trend()
        assert trend == "rising"

    def test_thermal_trend_stable(self):
        """Test stable thermal trend."""
        from hledac.universal.coordinators.memory_coordinator import ThermalState, UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator()
        coord._thermal_history = [
            (time.time() - 20, ThermalState.NORMAL),
            (time.time() - 10, ThermalState.NORMAL),
            (time.time(), ThermalState.NORMAL),
        ]

        trend = coord.get_thermal_trend()
        assert trend == "stable"


class TestMemoryPressure:
    """Test memory pressure handling."""

    def test_memory_pressure_ok_returns_bool(self):
        """Test _memory_pressure_ok returns bool."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()
        # Method exists and returns bool
        result = orch._memory_pressure_ok_sync(threshold=3.0)
        assert isinstance(result, bool)

    def test_cached_available_mb_init(self):
        """Test _cached_available_mb is initialized."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_cached_available_mb')


class TestMLXMetrics:
    """Test MLX metrics collection."""

    def test_get_mlx_metrics_returns_dict(self):
        """Test _get_mlx_metrics returns dict."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        metrics = orch._get_mlx_metrics()

        assert isinstance(metrics, dict)
        # Should have keys or be empty (fallback)
        assert 'active' in metrics or 'peak' in metrics or metrics == {}

    def test_mlx_metrics_version_guards(self):
        """Test MLX version guards in metrics."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Test with MLX unavailable - should return empty dict
        import sys
        original_mlx = sys.modules.get('mlx.core')

        # Temporarily remove mlx.core
        sys.modules['mlx.core'] = None
        try:
            metrics = orch._get_mlx_metrics()
            assert isinstance(metrics, dict)
        finally:
            if original_mlx:
                sys.modules['mlx.core'] = original_mlx
            elif 'mlx.core' in sys.modules:
                del sys.modules['mlx.core']


class TestActionRegistry:
    """Test action registry exists."""

    def test_action_registry_initialized(self):
        """Test _action_registry is initialized."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_action_registry')
        assert isinstance(orch._action_registry, dict)

    def test_thermal_penalty_initialized(self):
        """Test thermal penalty dicts are initialized."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        assert hasattr(orch, '_action_thermal_penalty')
        assert hasattr(orch, '_action_thermal_impact')
        assert isinstance(orch._action_thermal_penalty, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
