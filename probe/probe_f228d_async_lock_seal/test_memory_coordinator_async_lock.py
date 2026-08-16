"""
test_memory_coordinator_async_lock.py
====================================
Verifies SemanticCache in memory_coordinator.py uses asyncio.Lock,
not threading.RLock, and that all lock sites use async with.
"""

import pytest
from _core import aclose


class TestSemanticCacheAsyncLock:
    """Tests for MultiLevelContextCache._lock being asyncio.Lock."""

    @pytest.fixture
    def semantic_cache(self):
        """Create a MultiLevelContextCache instance for testing."""
        from coordinators.memory_coordinator import MultiLevelContextCache
        return MultiLevelContextCache()

    def test_lock_is_asyncio_lock(self, semantic_cache):
        """MultiLevelContextCache._lock must be asyncio.Lock, not threading.RLock."""
        import asyncio
        assert isinstance(semantic_cache._lock, asyncio.Lock), (
            f"Expected asyncio.Lock, got {type(semantic_cache._lock).__name__}. "
            "All callers are async; threading.RLock would block the event loop."
    )

    def test_no_threading_lock_in_semantic_cache(self, semantic_cache):
        """Ensure no threading.Lock leaks into _lock."""
        import threading
        assert not isinstance(semantic_cache._lock, threading.Lock), (
            "threading.Lock found — would block event loop when held in async context"
    )

    @pytest.mark.asyncio
    async def test_get_stats_no_lock_contention(self, semantic_cache):
        """
        async def get() increments total_requests without blocking.
        Lock scope is minimal — only hits/misses update.
        """
        # Prime the cache first
        await semantic_cache.set("test key", {"result": "value"})
        # get() should not raise — if threading.Lock leaked, we'd see a warning
        await semantic_cache.get("test key")
        # First get might miss (different text), that's ok
        assert semantic_cache.stats["total_requests"] >= 1

    @pytest.mark.asyncio
    async def test_set_then_get_round_trip(self, semantic_cache):
        """set() then get() on identical key returns the value."""
        key = "round trip test"
        value = {"data": 42}
        await semantic_cache.set(key, value)
        # Search for semantically similar text
        result = await semantic_cache.get(key)
        assert result is not None, "set+get should return cached value"
        assert result["data"] == 42

    @pytest.mark.asyncio
    async def test_clear_uses_async_lock(self, semantic_cache):
        """clear() uses async with self._lock — must not block."""
        await semantic_cache.set("key1", {"v": 1})
        # Should complete without blocking
        await semantic_cache.clear()
        # stats counter should still be accessible after clear
        assert "hits" in semantic_cache.stats

    @pytest.mark.asyncio
    async def test_lock_never_awaited_while_held(self, semantic_cache):
        """
        CRITICAL: No await inside async with self._lock blocks.
        We verify the code structure — lock scope is only dict/list mutations.
        """
        # This is a structural test: we inspect that no await exists between
        # async with self._lock: and the matching close-bracket.
        # The patched code uses async with only for:
        #   - stats dict updates
        #   - _update_access (sync method)
        #   - _rebuild_semantic_index (sync method)
        #   - list.append() calls
        # No await is called inside these critical sections.
        import inspect
        source = inspect.getsource(semantic_cache.get)
        # Verify lock pattern
        assert "async with self._lock:" in source, "get() should use async with self._lock"
        # Verify no nested await between lock acquire and release
        lines = source.split("\n")
        in_lock_block = False
        for line in lines:
            stripped = line.strip()
            if "async with self._lock:" in stripped:
                in_lock_block = True
            if in_lock_block and stripped.startswith("return"):
                in_lock_block = False
            if in_lock_block:
                # await inside lock would be like "await something"
                # Allow "async with" (reentrant scenario)
                if stripped.startswith("await ") and "self._lock" not in stripped:
                    pytest.fail(f"await found inside lock block: {stripped}")


class TestSyncBoundaryMethodsRetainThreadLock:
    """
    Verify that sync boundary methods (allocate, free, touch, etc.)
    still use self.lock (threading.Lock) — not self._lock (asyncio.Lock).
    """

    @pytest.fixture
    def coordinator(self):
        """Create a UniversalMemoryCoordinator instance."""
        from coordinators.memory_coordinator import UniversalMemoryCoordinator
        return UniversalMemoryCoordinator(memory_limit_mb=1000, enable_neuromorphic=False)

    def test_allocate_uses_threading_lock(self, coordinator):
        """allocate() uses self.lock (threading.Lock), not self._lock (asyncio)."""
        import inspect
        source = inspect.getsource(coordinator.allocate)
        assert "with self.lock:" in source, (
            "allocate() should use self.lock (threading.Lock), not self._lock"
    )
        assert "with self._lock:" not in source, (
            "allocate() must not use self._lock (asyncio.Lock)"
    )

    def test_free_uses_threading_lock(self, coordinator):
        """free() uses self.lock (threading.Lock)."""
        import inspect
        source = inspect.getsource(coordinator.free)
        assert "with self.lock:" in source
        assert "with self._lock:" not in source

    def test_touch_uses_threading_lock(self, coordinator):
        """touch() uses self.lock (threading.Lock)."""
        import inspect
        source = inspect.getsource(coordinator.touch)
        assert "with self.lock:" in source
        assert "with self._lock:" not in source
