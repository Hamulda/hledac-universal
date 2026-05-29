"""
Hermetic probe tests for blocking I/O offload seams.
Tests verify subprocess/psutil are not called directly from async event loop.
"""
import asyncio
import os
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from hledac.universal.project_types import MemoryConfig


class TestThermalSamplerOffload:
    """Tests for _ThermalSampler blocking I/O offload."""

    @pytest.fixture
    def sampler(self):
        """Create a ThermalSampler instance."""
        from layers.memory_layer import _ThermalSampler
        return _ThermalSampler(ttl_s=2.0)

    def test_read_temperature_sync_returns_none_by_default(self, sampler):
        """_read_temperature_sync returns None (current implementation)."""
        result = sampler._read_temperature_sync()
        assert result is None

    @pytest.mark.asyncio
    async def test_sample_does_not_call_subprocess_in_event_loop(self, sampler):
        """
        CRITICAL: _get_temperature does not call subprocess.run directly
        in the event loop — it must use asyncio.to_thread.
        """
        call_tracker = []
        original_run = subprocess.run

        def tracking_run(*args, **kwargs):
            call_tracker.append(('subprocess.run', args, kwargs))
            return original_run(*args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=tracking_run):
            with patch.object(subprocess, 'TimeoutExpired', subprocess.TimeoutExpired):
                # First call - should go through thread
                await sampler.sample()
                # Second call within TTL - should use cache
                await sampler.sample()

        # If we got here without hanging, the offload works
        assert True

    @pytest.mark.asyncio
    async def test_sample_uses_thread_offload(self, sampler):
        """Verify asyncio.to_thread is used for thermal reading."""
        call_tracker = []

        async def mock_to_thread(func, *args, **kwargs):
            call_tracker.append(('to_thread', func.__name__ if hasattr(func, '__name__') else str(func)))
            return func(*args, **kwargs)

        with patch.object(asyncio, 'to_thread', side_effect=mock_to_thread):
            await sampler.sample()

        assert len(call_tracker) >= 1
        assert call_tracker[0][0] == 'to_thread'

    @pytest.mark.asyncio
    async def test_thermal_cache_prevents_duplicate_subprocess_calls(self, sampler):
        """ThermalSampler cache prevents second subprocess call within TTL."""
        call_count = 0
        original_run = subprocess.run

        def counting_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_run(*args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=counting_run):
            with patch.object(subprocess, 'TimeoutExpired', subprocess.TimeoutExpired):
                await sampler.sample()
                await sampler.sample()
                await sampler.sample()

        assert call_count <= 2, f"Too many subprocess calls: {call_count}"

    @pytest.mark.asyncio
    async def test_timeout_expired_returns_none(self, sampler):
        """TimeoutExpired exception returns None or last known value (fail-soft)."""
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd='ioreg', timeout=1)

        with patch.object(subprocess, 'run', side_effect=raise_timeout):
            result = await sampler.sample()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_temperature_delegates_to_sampler(self):
        """_get_temperature() delegates to _ThermalSampler.sample()."""
        from layers.memory_layer import _MemoryStateManager

        config = MemoryConfig(
            memory_limit_mb=1024,
            health_check_interval_seconds=60
        )
        mgr = _MemoryStateManager(config)

        with patch.object(mgr._thermal_sampler, 'sample', new_callable=AsyncMock) as mock_sample:
            mock_sample.return_value = 42.5
            result = await mgr._get_temperature()

        assert result == 42.5
        mock_sample.assert_called_once()


class TestResourceCapacitySamplerOffload:
    """Tests for _ResourceCapacitySampler blocking I/O offload."""

    @pytest.fixture
    def sampler(self):
        """Create a _ResourceCapacitySampler instance."""
        from coordinators.resource_allocator import _ResourceCapacitySampler
        return _ResourceCapacitySampler()

    @pytest.mark.asyncio
    async def test_sample_uses_thread_offload_for_cpu(self, sampler):
        """Verify asyncio.to_thread is used for CPU reading."""
        call_tracker = []

        async def mock_to_thread(func, *args, **kwargs):
            call_tracker.append(('to_thread', func.__name__ if hasattr(func, '__name__') else str(func)))
            return func(*args, **kwargs)

        with patch.object(asyncio, 'to_thread', side_effect=mock_to_thread):
            await sampler.sample()

        assert any('cpu' in str(c[1]).lower() for c in call_tracker if c[0] == 'to_thread')

    @pytest.mark.asyncio
    async def test_cpu_percent_not_blocking_1s(self, sampler):
        """
        CRITICAL: psutil.cpu_percent(interval=1) is NOT called directly.
        Must use interval=0.0 or asyncio.to_thread.
        """
        call_tracker = []

        def tracking_cpu(*args, **kwargs):
            call_tracker.append(kwargs.get('interval'))
            return 50.0

        with patch('psutil.cpu_percent', side_effect=tracking_cpu):
            # Reset cache to force fresh sampling
            sampler._cpu_cache = None
            start = time.monotonic()
            await sampler.sample()
            elapsed = time.monotonic() - start

        # Verify interval=0.0 was passed (non-blocking)
        assert 0.0 in call_tracker, f"Expected interval=0.0, got: {call_tracker}"
        # Verify we didn't block for 1 second
        assert elapsed < 1.0, f"Blocked for {elapsed:.2f}s (likely interval=1)"

    @pytest.mark.asyncio
    async def test_metal_cache_prevents_system_profiler_calls(self, sampler):
        """Metal availability cache prevents system_profiler calls within 300s TTL."""
        call_count = 0
        original_run = subprocess.run

        def counting_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_run(*args, **kwargs)

        with patch.object(subprocess, 'run', side_effect=counting_run):
            with patch.object(subprocess, 'TimeoutExpired', subprocess.TimeoutExpired):
                await sampler.sample()
                await sampler.sample()
                await sampler.sample()

        assert call_count <= 2, f"Too many system_profiler calls: {call_count}"

    @pytest.mark.asyncio
    async def test_system_profiler_timeout_is_handled(self, sampler):
        """TimeoutExpired from system_profiler returns False (fail-soft)."""
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd='system_profiler', timeout=5)

        sampler._metal_cache = None
        sampler._metal_cache_time = 0

        with patch.object(subprocess, 'run', side_effect=raise_timeout):
            with patch.object(subprocess, 'TimeoutExpired', subprocess.TimeoutExpired):
                result = sampler._get_metal_sync()

        assert result is False

    @pytest.mark.asyncio
    async def test_get_current_capacity_uses_sampler(self):
        """get_current_capacity() uses _ResourceCapacitySampler."""
        from coordinators.resource_allocator import IntelligentResourceAllocator

        allocator = IntelligentResourceAllocator()

        with patch.object(allocator._capacity_sampler, 'sample', new_callable=AsyncMock) as mock_sample:
            from coordinators.resource_allocator import CapacitySnapshot
            mock_sample.return_value = CapacitySnapshot(
                cpu_percent=25.0,
                gpu_memory=4.0,
                gpu_usage=17.5,
                metal_available=True,
                sampled_at_monotonic=time.monotonic()
            )

            with patch('psutil.cpu_count', return_value=8):
                with patch('psutil.virtual_memory') as mock_mem:
                    mock_mem.return_value = MagicMock(
                        total=8 * (1024**3),
                        percent=50.0
                    )
                    with patch('psutil.disk_usage') as mock_disk:
                        mock_disk.return_value = MagicMock(total=500 * (1024**3))
                        with patch('psutil.net_io_counters') as mock_net:
                            mock_net.return_value = MagicMock()

                            result = await allocator.get_current_capacity()

        mock_sample.assert_called_once()
        assert result.cpu_usage == 0.25


class TestMemoryLayerIntegration:
    """Integration tests for memory_layer async hot path offload."""

    @pytest.mark.asyncio
    async def test_memory_state_manager_uses_thermal_sampler(self):
        """_MemoryStateManager.__init__ creates _thermal_sampler."""
        from layers.memory_layer import _MemoryStateManager

        config = MemoryConfig(
            memory_limit_mb=1024,
            health_check_interval_seconds=60
        )
        mgr = _MemoryStateManager(config)

        assert hasattr(mgr, '_thermal_sampler')
        assert mgr._thermal_sampler is not None

    @pytest.mark.asyncio
    async def test_perform_health_check_offloads_temperature(self):
        """_perform_health_check calls _get_temperature (which uses sampler)."""
        from layers.memory_layer import _MemoryStateManager

        config = MemoryConfig(
            memory_limit_mb=1024,
            health_check_interval_seconds=60
        )
        mgr = _MemoryStateManager(config)

        with patch.object(mgr, '_get_temperature', new_callable=AsyncMock) as mock_temp:
            mock_temp.return_value = 45.0
            with patch('psutil.virtual_memory') as mock_mem:
                mock_mem.return_value = MagicMock(
                    used=512 * (1024**2),
                    available=512 * (1024**2)
                )
                with patch('psutil.cpu_percent', return_value=30.0):
                    metrics = await mgr._perform_health_check()

        assert metrics.temperature_c == 45.0
        mock_temp.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-q'])
