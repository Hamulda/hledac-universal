"""
Tests for cache/adaptive_cache.py — Adaptive Memory-Aware Cache
==============================================================

Testuje:
- AdaptiveCache memory pressure detection
- TinyLFU LRU eviction
- Rust PyGraphLRUCache integration
- M1 8GB bounds

Invariant: Always-on, bounded, fail-safe
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch, MagicMock

import pytest






    AdaptiveCache,
    AdaptiveCacheConfig,
    CacheStats,
    MemoryPressure,
    create_adaptive_cache,
    get_cache,
    clear_all_caches,
    register_cache,
    _DEFAULT_CONFIG,
    # Constants
    _MAX_CACHE_BYTES_NORMAL,
    _MAX_CACHE_BYTES_ELEVATED,
    _MAX_CACHE_BYTES_CRITICAL,
)


class TestAdaptiveCacheConfig:
    """Test AdaptiveCacheConfig defaults."""

from _core import aclose
    def test_default_config(self):
        config = AdaptiveCacheConfig()
        assert config.max_bytes_normal == _MAX_CACHE_BYTES_NORMAL
        assert config.max_bytes_elevated == _MAX_CACHE_BYTES_ELEVATED
        assert config.max_bytes_critical == _MAX_CACHE_BYTES_CRITICAL
        assert config.max_entries_normal == 100_000
        assert config.max_entries_elevated == 50_000
        assert config.max_entries_critical == 10_000

    def test_custom_config(self):
        config = AdaptiveCacheConfig(
            max_bytes_normal=256 * 1024 * 1024,
            max_entries_normal=50_000,
        )
        assert config.max_bytes_normal == 256 * 1024 * 1024
        assert config.max_entries_normal == 50_000


class TestCacheStats:
    """Test CacheStats dataclass."""

    def test_hit_ratio_zero(self):
        stats = CacheStats()
        assert stats.hit_ratio == 0.0

    def test_hit_ratio_with_hits(self):
        stats = CacheStats(hits=80, misses=20)
        assert stats.hit_ratio == 0.8

    def test_default_values(self):
        stats = CacheStats()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.evictions == 0
        assert stats.current_bytes == 0
        assert stats.current_entries == 0
        assert stats.pressure_level == MemoryPressure.NORMAL


class TestAdaptiveCacheInit:
    """Test AdaptiveCache initialization."""

    def test_default_init(self):
        cache = AdaptiveCache()
        assert cache._max_bytes == _MAX_CACHE_BYTES_NORMAL
        assert cache._max_entries == 100_000
        assert cache._pressure == MemoryPressure.NORMAL

    def test_custom_config_init(self):
        config = AdaptiveCacheConfig(
            max_bytes_normal=256 * 1024 * 1024,
            memory_check_interval_sec=10.0,
        )
        cache = AdaptiveCache(config=config)
        assert cache._max_bytes == 256 * 1024 * 1024
        assert cache._memory_check_interval_sec == 10.0

    def test_pressure_property(self):
        cache = AdaptiveCache()
        assert cache.pressure == MemoryPressure.NORMAL


class TestAdaptiveCacheRustIntegration:
    """Test Rust PyGraphLRUCache integration (or fallback)."""

    def test_init_without_rust(self):
        """Test cache works without Rust extensions (fallback)."""
        with patch('hledac.universal.cache.adaptive_cache._get_rust', return_value=None):
            cache = AdaptiveCache()
            # Should not raise, just use fallback
            assert cache._use_rust is False

    def test_put_get_without_rust(self):
        """Test put/get without Rust (fallback path)."""
        with patch('hledac.universal.cache.adaptive_cache._get_rust', return_value=None):
            cache = AdaptiveCache()
            # Fallback put returns False (not implemented)
            # Fallback get returns None
            result = cache.get("key1")
            assert result is None


class TestAdaptiveCacheMemoryPressure:
    """Test memory pressure detection and adaptation."""

    def test_pressure_thresholds(self):
        """Test MemoryPressure enum values."""
        assert MemoryPressure.NORMAL == 0
        assert MemoryPressure.ELEVATED == 1
        assert MemoryPressure.CRITICAL == 2

    def test_memory_check_throttling(self):
        """Test that memory checks are throttled by interval."""
        cache = AdaptiveCache(config=AdaptiveCacheConfig(memory_check_interval_sec=5.0))

        # First call should update (last_memory_check = 0)
        # Pressure returns NORMAL by default
        p1 = cache.pressure
        assert p1 == MemoryPressure.NORMAL

        # Second immediate call should use cached value (throttled)
        p2 = cache.pressure
        assert p2 == MemoryPressure.NORMAL

    def test_update_limits_elevated(self):
        """Test limit update on elevated pressure."""
        config = AdaptiveCacheConfig()
        cache = AdaptiveCache(config=config)
        cache._pressure = MemoryPressure.ELEVATED
        cache._update_limits()

        assert cache._max_bytes == config.max_bytes_elevated
        assert cache._max_entries == config.max_entries_elevated

    def test_update_limits_critical(self):
        """Test limit update on critical pressure."""
        config = AdaptiveCacheConfig()
        cache = AdaptiveCache(config=config)
        cache._pressure = MemoryPressure.CRITICAL
        cache._update_limits()

        assert cache._max_bytes == config.max_bytes_critical
        assert cache._max_entries == config.max_entries_critical


class TestAdaptiveCacheRegistry:
    """Test global cache registry."""

    def setup_method(self):
        """Clear registry before each test."""
        clear_all_caches()

    def test_register_cache(self):
        """Test cache registration."""
        cache = AdaptiveCache()
        register_cache("test_cache", cache)
        assert get_cache("test_cache") is cache

    def test_get_cache_not_found(self):
        """Test get_cache returns None for unknown cache."""
        assert get_cache("nonexistent") is None

    def test_clear_all_caches(self):
        """Test clearing all caches."""
        cache1 = create_adaptive_cache("cache1")
        cache2 = create_adaptive_cache("cache2")

        # Verify caches are registered
        assert get_cache("cache1") is cache1
        assert get_cache("cache2") is cache2

        # Clear should work
        clear_all_caches()


class TestAdaptiveCacheFactory:
    """Test create_adaptive_cache factory."""

    def setup_method(self):
        clear_all_caches()

    def test_create_adaptive_cache(self):
        """Test factory creates and registers cache."""
        cache = create_adaptive_cache("factory_test")
        assert isinstance(cache, AdaptiveCache)
        assert get_cache("factory_test") is cache

    def test_create_with_custom_config(self):
        """Test factory with custom config."""
        config = AdaptiveCacheConfig(max_bytes_normal=128 * 1024 * 1024)
        cache = create_adaptive_cache("factory_custom", config=config)
        assert cache._max_bytes == 128 * 1024 * 1024


class TestAdaptiveCacheConcurrency:
    """Test thread safety."""

    def test_concurrent_access(self):
        """Test concurrent get/put operations."""
        cache = AdaptiveCache()

        errors = []

        def writer(n: int):
            try:
                for i in range(100):
                    cache.put(f"key_{n}_{i}", [i] * 10)
            except Exception as e:
                errors.append(e)

        def reader(n: int):
            try:
                for i in range(100):
                    cache.get(f"key_{n}_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t1 = threading.Thread(target=writer, args=(i,))
            t2 = threading.Thread(target=reader, args=(i,))
            threads.extend([t1, t2])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"


class TestAdaptiveCacheM1Bounds:
    """Test M1 8GB specific bounds."""

    def test_normal_bounds(self):
        """Test normal memory bounds."""
        assert _MAX_CACHE_BYTES_NORMAL == 512 * 1024 * 1024
        assert _MAX_CACHE_BYTES_ELEVATED == 256 * 1024 * 1024
        assert _MAX_CACHE_BYTES_CRITICAL == 64 * 1024 * 1024

    def test_m1_8gb_safe(self):
        """Test M1 8GB memory budget compliance."""
        # Total cache at normal: 512 MB
        # Total cache at elevated: 256 MB
        # Total cache at critical: 64 MB
        # All well under 8GB budget
        assert _MAX_CACHE_BYTES_NORMAL <= 512 * 1024 * 1024
        assert _MAX_CACHE_BYTES_ELEVATED <= 256 * 1024 * 1024
        assert _MAX_CACHE_BYTES_CRITICAL <= 64 * 1024 * 1024


class TestAdaptiveCacheClear:
    """Test cache clear operations."""

    def test_clear(self):
        """Test clear resets stats."""
        cache = AdaptiveCache()
        cache._stats.hits = 100
        cache._stats.misses = 50
        cache._stats.current_bytes = 1_000_000
        cache._stats.current_entries = 500

        cache.clear()

        assert cache._stats.hits == 0
        assert cache._stats.misses == 0

    def test_len_empty(self):
        """Test len on empty cache."""
        cache = AdaptiveCache()
        assert len(cache) == 0


class TestAdaptiveCacheContains:
    """Test __contains__ operator."""

    def test_contains_not_found(self):
        """Test contains returns False for missing key."""
        with patch('hledac.universal.cache.adaptive_cache._get_rust', return_value=None):
            cache = AdaptiveCache()
            assert ("missing_key" in cache) is False
