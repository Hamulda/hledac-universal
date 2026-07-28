"""
tests/test_brain_metal_device.py — MetalDevice Unit Tests
=========================================================

Dedikované testy pro brain/_metal/metal_device.py.
Testuje: MetalDevice, MetalMemoryStats, get_metal_device singleton.

M1 8GB invariant: Soft ceiling 5.5GiB, tier thresholds.
"""

from __future__ import annotations

import pytest


class TestMetalMemoryStats:
    """Test MetalMemoryStats dataclass."""

    def test_default_values(self) -> None:
        """Test default MetalMemoryStats fields."""
        from brain._metal.metal_device import MetalMemoryStats

        stats = MetalMemoryStats()
        assert stats.active_bytes == 0
        assert stats.active_gb == 0.0
        assert stats.peak_bytes == 0
        assert stats.peak_gb == 0.0
        assert stats.metal_tier == "unknown"
        assert stats.pressure_level == "normal"

    def test_frozen_immutable(self) -> None:
        """Test MetalMemoryStats is frozen (immutable)."""
        from brain._metal.metal_device import MetalMemoryStats

        stats = MetalMemoryStats(active_bytes=1024, metal_tier="high")
        with pytest.raises(Exception):  # frozen dataclass
            stats.active_bytes = 2048  # type: ignore

    def test_metal_tier_values(self) -> None:
        """Test MetalMemoryStats with non-default tier values."""
        from brain._metal.metal_device import MetalMemoryStats

        stats = MetalMemoryStats(
            active_bytes=2 * 1024**3,  # 2GB
            active_gb=2.0,
            peak_bytes=3 * 1024**3,
            peak_gb=3.0,
            metal_tier="critical",
            pressure_level="critical",
        )
        assert stats.metal_tier == "critical"
        assert stats.pressure_level == "critical"


class TestMetalDeviceDefaults:
    """Test MetalDevice tier thresholds and defaults."""

    def test_tier_thresholds_exist(self) -> None:
        """Test that tier thresholds are defined."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        thresholds = device.get_metal_tier_thresholds()

        assert "low" in thresholds
        assert "medium" in thresholds
        assert "high" in thresholds
        assert "critical" in thresholds

    def test_tier_thresholds_order(self) -> None:
        """Test tier thresholds are ordered low→critical."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        thresholds = device.get_metal_tier_thresholds()

        # Verify ascending order
        assert thresholds["low"][0] < thresholds["medium"][0]
        assert thresholds["medium"][0] < thresholds["high"][0]
        assert thresholds["high"][0] < thresholds["critical"][0]

    def test_tier_thresholds_values(self) -> None:
        """Test tier threshold boundary values."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        thresholds = device.get_metal_tier_thresholds()

        # low: 256MB - 512MB
        assert thresholds["low"][0] == 256 * 1024**2
        assert thresholds["low"][1] == 512 * 1024**2

        # medium: 512MB - 1GB
        assert thresholds["medium"][0] == 512 * 1024**2
        assert thresholds["medium"][1] == 1024 * 1024**2

        # high: 1GB - 2GB (CRITICAL FIX: was 2*1024**2**2 = ~256TB)
        assert thresholds["high"][0] == 1024 * 1024**2
        assert thresholds["high"][1] == 2 * 1024**3

        # critical: >2GB
        assert thresholds["critical"][0] == 2 * 1024**3
        assert thresholds["critical"][1] == float("inf")


class TestMetalDeviceGetMetalTier:
    """Test get_metal_tier() method."""

    def test_tier_low(self) -> None:
        """Test low tier (256MB-512MB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 300MB
        assert device.get_metal_tier(300 * 1024**2) == "low"

    def test_tier_medium(self) -> None:
        """Test medium tier (512MB-1GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 700MB
        assert device.get_metal_tier(700 * 1024**2) == "medium"

    def test_tier_high(self) -> None:
        """Test high tier (1GB-2GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 1.5GB
        assert device.get_metal_tier(1.5 * 1024**3) == "high"

    def test_tier_critical(self) -> None:
        """Test critical tier (>2GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 3GB
        assert device.get_metal_tier(3 * 1024**3) == "critical"

    def test_tier_boundary_256mb(self) -> None:
        """Test tier at 256MB boundary (inclusive low)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device.get_metal_tier(256 * 1024**2) == "low"

    def test_tier_boundary_512mb(self) -> None:
        """Test tier at 512MB boundary (exclusive high, inclusive medium)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device.get_metal_tier(512 * 1024**2) == "medium"

    def test_tier_boundary_1gb(self) -> None:
        """Test tier at 1GB boundary."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device.get_metal_tier(1024 * 1024**2) == "high"

    def test_tier_boundary_2gb(self) -> None:
        """Test tier at 2GB boundary (exclusive critical, inclusive high)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device.get_metal_tier(2 * 1024**3) == "critical"

    def test_tier_zero_memory(self) -> None:
        """Test tier at 0 bytes (should be low)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device.get_metal_tier(0) == "low"


class TestMetalDeviceGetPressureLevel:
    """Test _get_pressure_level() method."""

    def test_pressure_normal(self) -> None:
        """Test normal pressure (<512MB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device._get_pressure_level(300 * 1024**2) == "normal"

    def test_pressure_medium(self) -> None:
        """Test medium pressure (512MB-1GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device._get_pressure_level(700 * 1024**2) == "medium"

    def test_pressure_high(self) -> None:
        """Test high pressure (1GB-2GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device._get_pressure_level(1.5 * 1024**3) == "high"

    def test_pressure_critical(self) -> None:
        """Test critical pressure (>=2GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        assert device._get_pressure_level(2 * 1024**3) == "critical"


class TestMetalDeviceMemoryAccessors:
    """Test memory accessors (get_active_memory, get_peak_memory)."""

    def test_get_active_memory_returns_int(self) -> None:
        """Test get_active_memory returns integer bytes."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        result = device.get_active_memory()
        assert isinstance(result, int)
        assert result >= 0

    def test_get_peak_memory_returns_int(self) -> None:
        """Test get_peak_memory returns integer bytes."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        result = device.get_peak_memory()
        assert isinstance(result, int)
        assert result >= 0

    def test_get_stats_returns_stats_object(self) -> None:
        """Test get_stats returns MetalMemoryStats."""
        from brain._metal.metal_device import MetalDevice
        from brain._metal.metal_device import MetalMemoryStats

        device = MetalDevice()
        stats = device.get_stats()
        assert isinstance(stats, MetalMemoryStats)
        assert stats.active_bytes >= 0
        assert stats.peak_bytes >= 0


class TestMetalDeviceIsMemoryAvailable:
    """Test is_memory_available() method."""

    def test_is_memory_available_within_ceiling(self) -> None:
        """Test memory available check within soft ceiling (5.5GB)."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 1GB should be available (5.5GB ceiling)
        assert device.is_memory_available(1 * 1024**3) is True

    def test_is_memory_available_at_ceiling(self) -> None:
        """Test memory available check at soft ceiling."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # Current active + 5.5GB should be exactly at ceiling
        soft_ceiling = 5.5 * 1024**3
        # When active = 0, requesting 5.5GB should fit
        assert device.is_memory_available(soft_ceiling) is True

    def test_is_memory_available_exceeds_ceiling(self) -> None:
        """Test memory available check exceeds soft ceiling."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # 6GB request should exceed 5.5GB soft ceiling
        assert device.is_memory_available(6 * 1024**3) is False


class TestMetalDeviceSingleton:
    """Test get_metal_device() singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        """Test get_metal_device returns same instance."""
        from brain._metal.metal_device import get_metal_device, MetalDevice

        device1 = get_metal_device()
        device2 = get_metal_device()
        assert device1 is device2
        assert isinstance(device1, MetalDevice)

    def test_singleton_after_reset(self) -> None:
        """Test singleton returns new instance after reset."""
        import brain._metal.metal_device as module

        device1 = module.get_metal_device()
        module._device_instance = None
        device2 = module.get_metal_device()
        module._device_instance = device1  # restore

        assert device1 is not device2


class TestMetalDeviceM1Bounds:
    """M1 8GB UMA invariant tests."""

    def test_high_tier_max_is_2gb(self) -> None:
        """INVARIANT: high tier max must be 2GB (not 256TB).

        Previous bug: 2 * 1024**2**2 = ~256TB (exponent right-associative).
        Correct: 2 * 1024**3 = 2GB.
        """
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        thresholds = device.get_metal_tier_thresholds()

        # Bug was: 2 * 1024**2**2 = 2 * 1024**4 = ~256TB
        # Fixed: 2 * 1024**3 = 2GB
        assert thresholds["high"][1] == 2 * 1024**3
        assert thresholds["high"][1] < 100 * 1024**3  # Should NOT be TB scale

    def test_critical_tier_starts_at_2gb(self) -> None:
        """INVARIANT: critical tier must start at 2GB."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        thresholds = device.get_metal_tier_thresholds()

        assert thresholds["critical"][0] == 2 * 1024**3
        assert thresholds["critical"][0] == thresholds["high"][1]

    def test_soft_ceiling_5_5gb(self) -> None:
        """INVARIANT: soft ceiling must be 5.5GiB per M1 8GB budget."""
        from brain._metal.metal_device import MetalDevice

        device = MetalDevice()
        # Test is_memory_available uses 5.5GB ceiling internally
        # Request that would fit at 5.5GB but not at 6GB
        assert device.is_memory_available(5.5 * 1024**3) is True
        assert device.is_memory_available(5.5 * 1024**3 + 1) is False
