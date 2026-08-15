"""Tests for core/global_cache_registry.py (Issue #16).

Runs with: pytest tests/test_global_cache_registry.py -v
"""
from __future__ import annotations

import pytest

from hledac.universal.core.global_cache_registry import (
from core import aclose
    GlobalCacheRegistry,
    CacheEntry,
    register_cache,
    unregister_cache,
    clear_all_caches,
    get_cache_stats,
    list_registered_caches,
)


class TestGlobalCacheRegistry:
    """Test suite for GlobalCacheRegistry."""

    def setup_method(self) -> None:
        """Reset registry state before each test."""
        # Get the singleton and clear it
        registry = GlobalCacheRegistry.get_instance()
        registry._entries.clear()  # type: ignore[attr-defined]

    def test_singleton(self) -> None:
        """Registry is a true singleton."""
        r1 = GlobalCacheRegistry.get_instance()
        r2 = GlobalCacheRegistry.get_instance()
        assert r1 is r2

    def test_register_cache(self) -> None:
        """register_cache adds a cache to the registry."""
        register_cache("test1", get_size=lambda: 42, clear=lambda: None)
        assert "test1" in list_registered_caches()

    def test_register_cache_overwrite(self) -> None:
        """Registering the same name twice overwrites."""
        register_cache("dup", get_size=lambda: 1, clear=lambda: None)
        register_cache("dup", get_size=lambda: 2, clear=lambda: None)
        stats = get_cache_stats()
        assert stats["dup"]["size"] == 2

    def test_unregister_cache(self) -> None:
        """unregister_cache removes a cache."""
        register_cache("to_remove", get_size=lambda: 0, clear=lambda: None)
        assert unregister_cache("to_remove") is True
        assert "to_remove" not in list_registered_caches()

    def test_unregister_nonexistent(self) -> None:
        """unregister_cache returns False for missing cache."""
        assert unregister_cache("missing") is False

    def test_clear_all_caches(self) -> None:
        """clear_all_caches clears all registered caches and returns sizes."""
        clear_count = 0

        def make_clear():
            def clear():
                nonlocal clear_count
                clear_count += 1

            return clear

        register_cache("a", get_size=lambda: 10, clear=make_clear())
        register_cache("b", get_size=lambda: 20, clear=make_clear())

        sizes = clear_all_caches()

        assert sizes["a"] == 10
        assert sizes["b"] == 20
        assert clear_count == 2

    def test_clear_all_caches_failure(self) -> None:
        """clear_all_caches handles clear() exceptions gracefully."""
        register_cache(
            "fail", get_size=lambda: 1, clear=lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        sizes = clear_all_caches()
        assert sizes["fail"] == -1  # Failure indicator

    def test_get_cache_stats(self) -> None:
        """get_cache_stats returns correct structure."""
        register_cache(
            "stats_test",
            get_size=lambda: 99,
            clear=lambda: None,
            description="test cache",
        )
        stats = get_cache_stats()
        assert stats["stats_test"]["size"] == 99
        assert stats["stats_test"]["threshold"] == 0.85
        assert stats["stats_test"]["description"] == "test cache"

    def test_list_registered_caches_sorted(self) -> None:
        """list_registered_caches returns sorted list."""
        register_cache("z", get_size=lambda: 0, clear=lambda: None)
        register_cache("a", get_size=lambda: 0, clear=lambda: None)
        register_cache("m", get_size=lambda: 0, clear=lambda: None)
        assert list_registered_caches() == ["a", "m", "z"]

    def test_cache_entry_dataclass(self) -> None:
        """CacheEntry stores all fields correctly."""
        entry = CacheEntry(
            name="test",
            get_size=lambda: 5,
            clear=lambda: None,
            memory_pressure_threshold=0.7,
            _description="desc",
        )
        assert entry.name == "test"
        assert entry.memory_pressure_threshold == 0.7
        assert entry._description == "desc"

    def test_empty_registry_stats(self) -> None:
        """get_cache_stats returns empty dict when no caches registered."""
        stats = get_cache_stats()
        assert stats == {}

    def test_len(self) -> None:
        """len(registry) returns count of entries."""
        registry = GlobalCacheRegistry.get_instance()
        initial_len = len(registry)
        register_cache("new1", get_size=lambda: 0, clear=lambda: None)
        register_cache("new2", get_size=lambda: 0, clear=lambda: None)
        assert len(registry) == initial_len + 2
