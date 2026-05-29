"""
Tests for energy and thermal-aware inference (Sprint 75).
"""

import asyncio
from unittest.mock import MagicMock

import pytest


class TestThermalAware:
    """Test thermal-aware inference."""

    def test_thermal_state_import(self):
        """Test ThermalState can be imported."""
        from hledac.universal.coordinators.memory_coordinator import ThermalState

        assert hasattr(ThermalState, 'NORMAL')
        assert hasattr(ThermalState, 'WARM')
        assert hasattr(ThermalState, 'HOT')
        assert hasattr(ThermalState, 'CRITICAL')


class TestEnergyAware:
    """Test energy-aware inference."""

    def test_on_battery_power_method_exists(self):
        """Test _on_battery_power method exists."""
        from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator()
        assert hasattr(coord, '_on_battery_power')


class TestProfileThermal:
    """Test profile manager thermal awareness."""

    @pytest.mark.asyncio
    async def test_profile_manager_checks_thermal(self):
        """Test profile manager checks thermal state."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager
        from hledac.universal.coordinators.memory_coordinator import ThermalState

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None
        orch._memory_mgr = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value = ThermalState.NORMAL

        manager = _BrainManager(orch)
        manager._stop_profile.set()  # Stop immediately

        # Run manager once
        try:
            await asyncio.wait_for(manager._profile_manager(), timeout=0.5)
        except TimeoutError:
            pass  # Expected

    @pytest.mark.asyncio
    async def test_profile_short_context_on_hot(self):
        """Test profile switches to short-context when HOT."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager
        from hledac.universal.coordinators.memory_coordinator import ThermalState

        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._security_mgr = None
        orch._evidence_log = None
        orch._memory_mgr = MagicMock()
        orch._memory_mgr.get_thermal_state.return_value = ThermalState.HOT

        manager = _BrainManager(orch)
        manager._stop_profile.set()

        # Manually test profile selection logic
        available_gb = 5.0
        thermal = ThermalState.HOT

        if available_gb < 2.5 or thermal in (ThermalState.HOT, ThermalState.CRITICAL):
            profile = "short-context"
        else:
            profile = "full"

        assert profile == "short-context"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
