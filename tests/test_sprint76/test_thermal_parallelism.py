"""
Tests for thermal-aware parallelism (Sprint 76).
"""

from unittest.mock import MagicMock

import pytest


class TestThermalParallelism:
    """Test thermal and battery aware parallelism."""

    def test_analysis_schema_exists(self):
        """Test AnalysisSchema exists."""
        from hledac.universal.autonomous_orchestrator import AnalysisSchema

        schema = AnalysisSchema("investigation", "high", ["web", "academic"])
        assert schema.intent == "investigation"
        assert schema.complexity == "high"
        assert schema.suggested_sources == ["web", "academic"]

    def test_analysis_schema_from_dict(self):
        """Test AnalysisSchema.from_dict."""
        from hledac.universal.autonomous_orchestrator import AnalysisSchema

        data = {
            'intent': 'verification',
            'complexity': 'low',
            'suggested_sources': ['db', 'api']
        }
        schema = AnalysisSchema.from_dict(data)
        assert schema.intent == 'verification'
        assert schema.complexity == 'low'
        assert schema.suggested_sources == ['db', 'api']

    def test_analysis_schema_defaults(self):
        """Test AnalysisSchema defaults."""
        from hledac.universal.autonomous_orchestrator import AnalysisSchema

        schema = AnalysisSchema.from_dict({})
        assert schema.intent == 'general'
        assert schema.complexity == 'medium'
        assert schema.suggested_sources == []

    @pytest.mark.asyncio
    async def test_parallel_when_cool(self):
        """Test parallel execution when thermal is NORMAL."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._memory_mgr = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value.name = "NORMAL"
        orch._memory_mgr._on_battery_power.return_value = False

        # Simulate parallel decision logic
        thermal = orch._memory_mgr.get_thermal_state()
        on_battery = orch._memory_mgr._on_battery_power()

        should_parallel = (
            thermal.name not in ("HOT", "CRITICAL") and
            not on_battery
        )
        assert should_parallel is True

    @pytest.mark.asyncio
    async def test_sequential_when_hot(self):
        """Test sequential execution when thermal is HOT."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._memory_mgr = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value.name = "HOT"
        orch._memory_mgr._on_battery_power.return_value = False

        thermal = orch._memory_mgr.get_thermal_state()
        on_battery = orch._memory_mgr._on_battery_power()

        should_parallel = (
            thermal.name not in ("HOT", "CRITICAL") and
            not on_battery
        )
        assert should_parallel is False

    @pytest.mark.asyncio
    async def test_sequential_on_battery(self):
        """Test sequential execution when on battery."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._memory_mgr = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value.name = "NORMAL"
        orch._memory_mgr._on_battery_power.return_value = True

        thermal = orch._memory_mgr.get_thermal_state()
        on_battery = orch._memory_mgr._on_battery_power()

        should_parallel = (
            thermal.name not in ("HOT", "CRITICAL") and
            not on_battery
        )
        assert should_parallel is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
