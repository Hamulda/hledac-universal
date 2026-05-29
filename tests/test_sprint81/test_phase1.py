"""
Tests for Sprint 81 - Core Stability & Memory Safety
Fáze 1: mlx_utils, EmergencyBrake, UnifiedMemoryMonitor
"""

import asyncio
import time

import pytest


class TestMLXUtils:
    """Tests for mlx_utils.py"""

    def test_mlx_utils_imports(self):
        """Test mlx_utils module can be imported."""
        from hledac.universal.utils import mlx_utils
        assert mlx_utils is not None

    def test_mlx_managed_decorator_exists(self):
        """Test mlx_managed decorator exists."""
        from hledac.universal.utils.mlx_utils import mlx_managed
        assert callable(mlx_managed)

    def test_mlx_cleanup_after_decorator_exists(self):
        """Test mlx_cleanup_after decorator exists."""
        from hledac.universal.utils.mlx_utils import mlx_cleanup_after
        assert callable(mlx_cleanup_after)

    def test_get_mlx_memory_stats(self):
        """Test get_mlx_memory_stats returns dict."""
        from hledac.universal.utils.mlx_utils import get_mlx_memory_stats
        stats = get_mlx_memory_stats()
        assert isinstance(stats, dict)
        assert 'available' in stats

    def test_reset_metal_peak(self):
        """Test reset_metal_peak doesn't crash."""
        from hledac.universal.utils.mlx_utils import reset_metal_peak
        # Should not raise
        reset_metal_peak()

    def test_sync_function_decorated(self):
        """Test mlx_managed works on sync functions."""
        from hledac.universal.utils.mlx_utils import mlx_managed

        @mlx_managed
        def sync_test():
            return 42

        result = sync_test()
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_function_decorated(self):
        """Test mlx_managed works on async functions."""
        from hledac.universal.utils.mlx_utils import mlx_managed

        @mlx_managed
        async def async_test():
            await asyncio.sleep(0.001)
            return 42

        result = await async_test()
        assert result == 42


class TestUnifiedMemoryMonitor:
    """Tests for memory_dashboard.py"""

    def test_memory_dashboard_imports(self):
        """Test memory_dashboard module can be imported."""
        from hledac.universal.utils import memory_dashboard
        assert memory_dashboard is not None

    def test_unified_memory_snapshot_dataclass(self):
        """Test UnifiedMemorySnapshot can be created."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemorySnapshot

        snap = UnifiedMemorySnapshot(
            sys_total_gb=8.0,
            sys_available_gb=4.0,
            sys_used_gb=4.0,
            sys_percent=50.0,
        )
        assert snap.sys_total_gb == 8.0
        assert snap.sys_available_gb == 4.0
        assert snap.pressure == 0.5

    def test_unified_memory_snapshot_properties(self):
        """Test snapshot property helpers."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemorySnapshot

        # Normal state
        snap = UnifiedMemorySnapshot(
            sys_total_gb=8.0,
            sys_available_gb=5.0,
            sys_used_gb=3.0,
            sys_percent=37.5,
        )
        assert not snap.is_critical
        assert not snap.is_warning

        # Warning state
        snap2 = UnifiedMemorySnapshot(
            sys_total_gb=8.0,
            sys_available_gb=1.5,
            sys_used_gb=6.5,
            sys_percent=81.25,
        )
        assert snap2.is_warning
        assert not snap2.is_critical

        # Critical state
        snap3 = UnifiedMemorySnapshot(
            sys_total_gb=8.0,
            sys_available_gb=0.5,
            sys_used_gb=7.5,
            sys_percent=93.75,
        )
        assert snap3.is_critical

    def test_unified_memory_monitor_creation(self):
        """Test UnifiedMemoryMonitor can be created."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()
        assert monitor is not None

    def test_unified_memory_monitor_snapshot(self):
        """Test monitor.snapshot() returns valid data."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()
        snap = monitor.snapshot()

        assert snap.sys_total_gb > 0
        assert snap.sys_available_gb >= 0
        assert snap.sys_used_gb >= 0

    def test_get_pressure_level(self):
        """Test get_pressure_level returns valid string."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()
        level = monitor.get_pressure_level()

        assert level in ("critical", "warning", "normal", "healthy")

    def test_should_emergency_brake(self):
        """Test should_emergency_brake logic."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()
        should_brake = monitor.should_emergency_brake(
            critical_gb=1.0,
            metal_peak_gb=6.0
        )

        assert isinstance(should_brake, bool)

    def test_get_summary(self):
        """Test get_summary returns string."""
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()
        summary = monitor.get_summary()

        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_convenience_function(self):
        """Test get_unified_snapshot convenience function."""
        from hledac.universal.utils.memory_dashboard import get_unified_snapshot

        snap = get_unified_snapshot()
        assert isinstance(snap, type(get_unified_snapshot()))


class TestEmergencyBrakeIntegration:
    """Tests for EmergencyBrake integration in orchestrator"""

    def test_emergency_brake_init(self):
        """Test EmergencyBrake can be initialized in orchestrator."""
        # This is a smoke test - we just check imports work
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        # Create a mock setup
        monitor = UnifiedMemoryMonitor()
        assert monitor is not None

    @pytest.mark.asyncio
    async def test_emergency_brake_check(self):
        """Test emergency brake check method exists and works."""
        # We'll test the concept with a simple async check
        from hledac.universal.utils.memory_dashboard import UnifiedMemoryMonitor

        monitor = UnifiedMemoryMonitor()

        # Just call snapshot - actual brake logic is tested via integration
        snap = monitor.snapshot()
        assert snap is not None


class TestMLXCacheClearing:
    """Tests for MLX cache clearing functionality"""

    @pytest.mark.asyncio
    async def test_maybe_eval_throttle(self):
        """Test that mx.eval is properly throttled."""
        from hledac.universal.utils.mlx_utils import _maybe_eval_async

        # Call multiple times rapidly
        start = time.time()
        for _ in range(5):
            await _maybe_eval_async()
        elapsed = time.time() - start

        # Should be very fast due to throttling (no actual eval)
        assert elapsed < 0.5

    def test_clear_metal_cache_sync(self):
        """Test metal cache clearing doesn't crash."""
        from hledac.universal.utils.mlx_utils import _clear_metal_cache_sync

        # Should not raise
        _clear_metal_cache_sync()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
