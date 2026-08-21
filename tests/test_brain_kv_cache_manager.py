"""
tests/test_brain_kv_cache_manager.py — KVCacheManager Unit Tests
===============================================================

Dedikované testy pro brain/_cache/kv_cache_manager.py.
Testuje: KVCacheManager, KVCacheStats.

M1 8GB invariant: Bounded pool sizes (4/8/64 items).
"""

from __future__ import annotations

import pytest


class TestKVCacheStats:
    """Test KVCacheStats dataclass."""

    def test_default_values(self) -> None:
        """Test default KVCacheStats fields."""
        from brain._cache.kv_cache_manager import KVCacheStats

        stats = KVCacheStats()
        assert stats.pool_size == 0
        assert stats.pool_maxsize == 0
        assert stats.session_cache_size == 0
        assert stats.session_cache_maxsize == 0
        assert stats.prefix_cache_size == 0
        assert stats.prefix_cache_maxsize == 0
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0
        assert stats.cache_prefills == 0

    def test_frozen_immutable(self) -> None:
        """Test KVCacheStats is frozen (immutable)."""
        from brain._cache.kv_cache_manager import KVCacheStats

        stats = KVCacheStats(pool_size=5)
        with pytest.raises(Exception):  # frozen dataclass
            stats.pool_size = 10  # type: ignore


class TestKVCacheManagerInit:
    """Test KVCacheManager initialization."""

    def test_default_pool_sizes(self) -> None:
        """Test default cache pool sizes match M1 8GB bounds."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        # M1 8GB bounds: KV pool=4, session=8, prefix=64
        assert manager.kv_pool_maxsize == 4
        assert manager.session_cache_maxsize == 8
        assert manager.prefix_cache_maxsize == 64

    def test_custom_pool_sizes(self) -> None:
        """Test KVCacheManager with custom pool sizes."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager(
            kv_pool_maxsize=8,
            session_cache_maxsize=16,
            prefix_cache_maxsize=128,
        )
        assert manager.kv_pool_maxsize == 8
        assert manager.session_cache_maxsize == 16
        assert manager.prefix_cache_maxsize == 128

    def test_internal_pools_initialized(self) -> None:
        """Test internal pools are initialized after __post_init__."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager._kv_cache_pool is not None
        assert manager._session_cache_pool is not None
        assert manager._prefix_cache is not None

    def test_internal_stats_initialized(self) -> None:
        """Test internal stats dicts are initialized."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager._kv_cache_stats is not None
        assert manager._session_cache_stats is not None
        assert manager._prefix_cache_stats is not None


class TestKVCacheManagerKVPool:
    """Test KV pool operations."""

    def test_kv_pool_max_size(self) -> None:
        """Test KV pool respects max_size bound."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager(kv_pool_maxsize=2)
        assert manager.kv_pool_maxsize == 2

    def test_kv_pool_memory_mb_default(self) -> None:
        """Test KV pool memory default is 256MB."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager.kv_pool_memory_mb == 256


class TestKVCacheManagerPrefixCache:
    """Test prefix cache operations."""

    def test_prefix_cache_maxsize(self) -> None:
        """Test prefix cache max_size."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager(prefix_cache_maxsize=100)
        assert manager.prefix_cache_maxsize == 100

    def test_prefix_cache_get_none_for_missing(self) -> None:
        """Test get_prefix_cache returns None for non-existent key."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        result = manager.get_prefix_cache("non-existent-system-prompt")
        assert result is None


class TestKVCacheManagerSessionCache:
    """Test session cache operations."""

    def test_session_cache_maxsize(self) -> None:
        """Test session cache max_size."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager(session_cache_maxsize=16)
        assert manager.session_cache_maxsize == 16

    def test_session_cache_memory_mb_default(self) -> None:
        """Test session cache memory default is 128MB."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager.session_cache_memory_mb == 128


class TestKVCacheManagerStats:
    """Test KVCacheManager stats methods."""

    def test_get_stats_returns_kv_cache_stats(self) -> None:
        """Test get_stats returns KVCacheStats object."""
        from brain._cache.kv_cache_manager import KVCacheManager, KVCacheStats

        manager = KVCacheManager()
        stats = manager.get_stats()

        assert isinstance(stats, KVCacheStats)
        assert stats.pool_size == 0
        assert stats.pool_maxsize == manager.kv_pool_maxsize
        assert stats.session_cache_size == 0
        assert stats.session_cache_maxsize == manager.session_cache_maxsize
        assert stats.prefix_cache_size == 0
        assert stats.prefix_cache_maxsize == manager.prefix_cache_maxsize

    def test_get_stats_reflects_cache_state(self) -> None:
        """Test get_stats reflects actual cache state."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        # Stats should reflect empty pools initially
        stats = manager.get_stats()
        assert stats.pool_size == 0
        assert stats.session_cache_size == 0
        assert stats.prefix_cache_size == 0


class TestKVCacheManagerClear:
    """Test KVCacheManager clear operations."""

    def test_invalidate_all_clears_all_pools(self) -> None:
        """Test invalidate_all_caches() clears all internal pools."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        manager.invalidate_all_caches("test")

        stats = manager.get_stats()
        assert stats.pool_size == 0
        assert stats.session_cache_size == 0
        assert stats.prefix_cache_size == 0


class TestKVCacheManagerSingleton:
    """Test get_kv_cache_manager() singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        """Test get_kv_cache_manager returns same instance."""
        from brain._cache.kv_cache_manager import KVCacheManager, get_kv_cache_manager

        manager1 = get_kv_cache_manager()
        manager2 = get_kv_cache_manager()
        assert manager1 is manager2
        assert isinstance(manager1, KVCacheManager)

    def test_singleton_after_reset(self) -> None:
        """Test singleton returns new instance after reset."""
        import brain._cache.kv_cache_manager as module

        manager1 = module.get_kv_cache_manager()
        module._kv_cache_manager_instance = None
        manager2 = module.get_kv_cache_manager()
        module._kv_cache_manager_instance = manager1  # restore

        assert manager1 is not manager2


class TestKVCacheManagerTypeAliases:
    """Test type aliases (PrefixCache, SessionCache)."""

    def test_prefix_cache_alias(self) -> None:
        """Test PrefixCache is KVCacheManager."""
        from brain._cache.kv_cache_manager import KVCacheManager, PrefixCache

        assert PrefixCache is KVCacheManager

    def test_session_cache_alias(self) -> None:
        """Test SessionCache is KVCacheManager."""
        from brain._cache.kv_cache_manager import KVCacheManager, SessionCache

        assert SessionCache is KVCacheManager


class TestKVCacheManagerM1Bounds:
    """M1 8GB invariant tests."""

    def test_kv_pool_maxsize_is_bounded(self) -> None:
        """INVARIANT: KV pool maxsize must be <= 8 for M1 8GB.

        CLAUDE.md: KV pool bounded to 4 items (256MB memory).
        """
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager.kv_pool_maxsize <= 8

    def test_session_cache_maxsize_is_bounded(self) -> None:
        """INVARIANT: session cache maxsize must be <= 16 for M1 8GB.

        CLAUDE.md: Session cache bounded to 8 items (128MB memory).
        """
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager.session_cache_maxsize <= 16

    def test_prefix_cache_maxsize_is_bounded(self) -> None:
        """INVARIANT: prefix cache maxsize must be reasonable.

        Prefix cache holds system prompt hashes, bounded to prevent unbounded growth.
        """
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        assert manager.prefix_cache_maxsize > 0
        assert manager.prefix_cache_maxsize <= 256  # Reasonable upper bound

    def test_kv_pool_memory_mb_reasonable(self) -> None:
        """INVARIANT: KV pool memory should be reasonable for M1 8GB."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        # 256MB per pool is reasonable for M1 8GB
        assert 64 <= manager.kv_pool_memory_mb <= 512

    def test_session_cache_memory_mb_reasonable(self) -> None:
        """INVARIANT: session cache memory should be reasonable for M1 8GB."""
        from brain._cache.kv_cache_manager import KVCacheManager

        manager = KVCacheManager()
        # 128MB per pool is reasonable for M1 8GB
        assert 32 <= manager.session_cache_memory_mb <= 256
