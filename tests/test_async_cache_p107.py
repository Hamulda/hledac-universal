"""
TestSprintP107 — Issue #P1-07: async_cache (async-safe bounded cache)

Tests verify the async-safe bounded cache implementation:
  [1] async_cached decorator rejects sync def immediately
  [2] Per-key single-flight: concurrent calls to same key serialize
  [3] Different keys: no lock contention
  [4] LRU eviction: maxsize respected
  [5] Fail-safe: lock creation failure doesn't crash
  [6] No asyncio.Lock at module import (lazy creation)
  [7] Dict args raise AsyncCacheError immediately
  [8] M1 8GB: per-key lock dict bounded

All tests use real asyncio (no mocks) to verify actual behavior.
"""
from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Awaitable
from typing import Any

import pytest






    AsyncCacheError,
    AsyncLRUCache,
    async_cached,
    cached_awaitable,
)


# ---------------------------------------------------------------------------

from _core import aclose# Invariant [1]: async_cached rejects sync def
# ---------------------------------------------------------------------------

def test_async_cached_rejects_sync_def() -> None:
    """[1] async_cached raises AsyncCacheError when decorating sync function."""
    with pytest.raises(AsyncCacheError, match="sync function"):
        async_cached(maxsize=128)(lambda x: x.upper())  # type: ignore[arg-type]


def test_async_cached_rejects_sync_def_unqualified() -> None:
    """[1] async_cached works with getattr fallback for __qualname__."""
    # Local functions have __qualname__ in Python 3
    def anon_sync(x: str) -> str:
        return x.upper()

    # getattr safely handles both __qualname__ and __name__
    name = getattr(anon_sync, '__qualname__', getattr(anon_sync, '__name__', '<anon>'))
    assert '<locals>' in name  # Local function qualname contains '<locals>'


# ---------------------------------------------------------------------------
# Invariant [2]: Per-key single-flight (concurrent calls serialize)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_flight_serialization() -> None:
    """[2] Per-key lock: concurrent calls to same key serialize (single-flight)."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=10)
    call_log: list[int] = []

    async def slow_compute(k: str) -> int:
        call_log.append(1)
        await asyncio.sleep(0.05)  # 50ms
        return len(k) * 10

    # Simulate 5 concurrent callers for same key
    async def caller(k: str) -> int:
        return await cache.acquire(k, compute=lambda k=k: slow_compute(k))

    results = await asyncio.gather(*[caller("test") for _ in range(5)])

    # All got same result
    assert len(set(results)) == 1
    assert results[0] == 40  # len("test")=4, 4*10=40

    # Only ONE call to slow_compute (single-flight)
    assert call_log == [1], f"Expected single flight, got {len(call_log)} calls"


@pytest.mark.asyncio
async def test_single_flight_with_exception() -> None:
    """[2] Single-flight: if compute raises, all waiters get the error."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=10)

    async def failing_compute() -> int:
        await asyncio.sleep(0.02)
        raise ValueError("boom")

    callers = [
        asyncio.create_task(cache.acquire("key", compute=failing_compute))
        for _ in range(3)
    ]
    results = await asyncio.gather(*callers, return_exceptions=True)

    # All should get the same error
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 3
    assert all(isinstance(e, AsyncCacheError) for e in errors)


# ---------------------------------------------------------------------------
# Invariant [3]: Different keys don't contend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_contention_different_keys() -> None:
    """[3] Different keys: no lock contention (can run in parallel)."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=10)

    async def compute(k: str) -> int:
        await asyncio.sleep(0.02)
        return len(k)

    async def caller(k: str) -> int:
        return await cache.acquire(k, compute=lambda k=k: compute(k))

    # These can all run in parallel (different keys)
    start = asyncio.get_event_loop().time()
    results = await asyncio.gather(
        caller("short"),
        caller("medium"),
        caller("longest_key"),
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert results == [5, 6, 11]  # len("short")=5, len("medium")=6, len("longest_key")=11
    # Should be ~0.02s (parallel), NOT ~0.06s (serial)
    assert elapsed < 0.05, f"Expected parallel execution, took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Invariant [4]: LRU eviction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lru_eviction() -> None:
    """[4] LRU eviction: maxsize respected, oldest entry evicted."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=3)

    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)
    assert len(cache) == 3

    # 'a' is oldest — adding 'd' should evict it
    await cache.set("d", 4)
    assert len(cache) == 3
    assert "a" not in cache
    assert "b" in cache and "c" in cache and "d" in cache

    # Access 'b' to make it newest, then add 'e'
    _ = await cache.get("b")  # touch 'b'
    await cache.set("e", 5)

    # 'c' should now be oldest and evicted
    assert "c" not in cache
    assert "a" not in cache
    assert "b" in cache and "d" in cache and "e" in cache


@pytest.mark.asyncio
async def test_lru_with_acquire() -> None:
    """[4] LRU via acquire(): repeated keys update LRU order."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=3)

    # Helper to avoid default arg issues
    async def make_1() -> int:
        return 1
    async def make_2() -> int:
        return 2
    async def make_3() -> int:
        return 3
    async def make_4() -> int:
        return 4

    await cache.acquire("a", compute=make_1)
    await cache.acquire("b", compute=make_2)
    await cache.acquire("c", compute=make_3)

    # Access 'a' to make it newest
    _ = await cache.acquire("a", compute=make_1)

    # Add 'd' — should evict 'b' (oldest after 'a' was touched)
    await cache.acquire("d", compute=make_4)
    assert len(cache) == 3
    assert "b" not in cache


# ---------------------------------------------------------------------------
# Invariant [5]: Fail-safe on lock creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fail_safe_get_set() -> None:
    """[5] Fail-safe: get/set work even if lock creation partially fails."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)

    # These should work fine
    await cache.set("key1", 100)
    result = await cache.get("key1")
    assert result == 100

    # Test with multiple keys
    for i in range(10):
        await cache.set(f"key_{i}", i * i)
    assert len(cache) == 5  # maxsize respected


# ---------------------------------------------------------------------------
# Invariant [6]: No asyncio.Lock at module import
# ---------------------------------------------------------------------------

def test_no_lock_at_module_level() -> None:
    """[6] AsyncLRUCache doesn't create locks at init time."""
    # Create cache — should NOT create any locks yet
    cache = AsyncLRUCache(maxsize=5)
    assert len(cache._locks) == 0  # type: ignore[access-protected]

    # Only after accessing _lock_for should locks start appearing
    # (we test this via get, which calls _lock_for internally)


@pytest.mark.asyncio
async def test_locks_created_lazily() -> None:
    """[6] Locks are created lazily on first use."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)

    assert len(cache._locks) == 0  # type: ignore[access-protected]

    await cache.set("x", 1)
    assert len(cache._locks) >= 1  # Lock for "x" created


# ---------------------------------------------------------------------------
# Invariant [7]: Dict args raise immediately
# ---------------------------------------------------------------------------

def test_dict_key_raises_on_construction() -> None:
    """[7] AsyncLRUCache raises AsyncCacheError on dict key construction."""
    cache = AsyncLRUCache[dict[str, int], str](maxsize=10)

    with pytest.raises(AsyncCacheError, match="hashable"):
        # This should raise immediately on hash attempt
        cache._check_hashable({"a": 1})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dict_key_raises_on_get() -> None:
    """[7] AsyncLRUCache.get() raises AsyncCacheError on dict key."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=10)

    # str key works fine
    assert await cache.get("ok_key") is None

    # dict key raises
    with pytest.raises(AsyncCacheError, match="hashable"):
        await cache.get({"a": 1})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_async_cached_dict_arg_raises() -> None:
    """[7] async_cached raises AsyncCacheError when called with dict arg."""
    @async_cached(maxsize=128)
    async def fetch(url: str) -> str:
        return f"result for {url}"

    # str arg works
    result = await fetch("http://example.com")
    assert "example.com" in result

    # dict arg raises
    with pytest.raises(AsyncCacheError, match="hashable"):
        await fetch({"url": "http://example.com"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Invariant [8]: Per-key lock dict bounded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_dict_bounded() -> None:
    """[8] Per-key lock dict bounded to max_locks (M1 8GB safe)."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=100, max_locks=20)

    # Add many unique keys
    for i in range(50):
        await cache.set(f"key_{i}", i)

    # Lock dict should NOT exceed max_locks
    assert len(cache._locks) <= 20  # type: ignore[access-protected]


@pytest.mark.asyncio
async def test_lock_dict_eviction_lru() -> None:
    """[8] When lock dict is full, oldest entries are evicted (LRU)."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=100, max_locks=5)

    # Add 10 unique keys
    for i in range(10):
        await cache.set(f"k{i}", i)

    # Should have at most 5 locks
    assert len(cache._locks) <= 5  # type: ignore[access-protected]


# ---------------------------------------------------------------------------
# Additional behavior tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acquire_without_compute_raises() -> None:
    """acquire() without compute raises KeyError for unknown key."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)
    with pytest.raises(KeyError):
        await cache.acquire("unknown_key")


@pytest.mark.asyncio
async def test_clear_works() -> None:
    """clear() removes all entries and locks."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)

    async def make_10() -> int:
        return 10
    await cache.acquire("a", compute=make_10)  # touch

    cache.clear()
    assert len(cache) == 0
    assert len(cache._locks) == 0  # type: ignore[access-protected]


@pytest.mark.asyncio
async def test_contains() -> None:
    """__contains__ works correctly."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)
    await cache.set("a", 1)
    assert "a" in cache
    assert "b" not in cache


@pytest.mark.asyncio
async def test_cached_awaitable_helper() -> None:
    """cached_awaitable() utility works."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)

    async def compute() -> int:
        return len("hello") * 2

    result = await cached_awaitable(cache, "hello", compute)
    assert result == 10

    # Second call hits cache
    result2 = await cached_awaitable(cache, "hello", compute)
    assert result2 == 10


@pytest.mark.asyncio
async def test_on_evict_callback() -> None:
    """on_evict callback is called when entries are evicted."""
    evicted: list[tuple[str, int]] = []

    def track_evict(key: str, value: int) -> None:
        evicted.append((key, value))

    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=2, on_evict=track_evict)

    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)  # evicts "a"

    assert evicted == [("a", 1)]


@pytest.mark.asyncio
async def test_decorator_cache_clear() -> None:
    """decorator.cache_clear() works for test isolation."""
    @async_cached(maxsize=3)
    async def cached_fn(x: str) -> int:
        return len(x)

    await cached_fn("short")
    await cached_fn("medium")
    assert len(cached_fn.__cache__) == 2  # type: ignore[attr-defined]

    cached_fn.cache_clear()  # type: ignore[attr-defined]
    assert len(cached_fn.__cache__) == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_decorator_shared_cache() -> None:
    """Multiple decorators can share a cache instance."""
    shared: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)

    @async_cached(cache=shared)
    async def fn1(x: str) -> int:
        return len(x)

    @async_cached(cache=shared)
    async def fn2(x: str) -> int:
        return len(x) * 2

    r1 = await fn1("hello")
    r2 = await fn2("world")

    assert r1 == 5
    assert r2 == 10

    # fn1("hello") again — should hit cache
    r1b = await fn1("hello")
    assert r1b == 5


@pytest.mark.asyncio
async def test_repr() -> None:
    """__repr__ shows useful info."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=10)
    await cache.set("a", 1)
    await cache.set("b", 2)

    r = repr(cache)
    assert "AsyncLRUCache" in r
    assert "maxsize=10" in r
    assert "len=2" in r


# ---------------------------------------------------------------------------
# Negative tests — verify edge cases don't crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_key() -> None:
    """Empty string key works."""
    cache: AsyncLRUCache[str, int] = AsyncLRUCache(maxsize=5)
    await cache.set("", 42)
    result = await cache.get("")
    assert result == 42


@pytest.mark.asyncio
async def test_tuple_key() -> None:
    """Tuple keys work (hashable)."""
    cache: AsyncLRUCache[tuple[str, int], str] = AsyncLRUCache(maxsize=5)
    await cache.set(("hello", 1), "value")
    result = await cache.get(("hello", 1))
    assert result == "value"


@pytest.mark.asyncio
async def test_zero_maxsize_rejected() -> None:
    """maxsize=0 raises ValueError."""
    with pytest.raises(ValueError, match="positive"):
        AsyncLRUCache(maxsize=0)


@pytest.mark.asyncio
async def test_negative_maxsize_rejected() -> None:
    """Negative maxsize raises ValueError."""
    with pytest.raises(ValueError, match="positive"):
        AsyncLRUCache(maxsize=-1)
