"""
TestSprintF330: LazySingleton + AsyncLazySingleton tests (Issue #14)
===================================================================

Tests:
  1. LazySingleton  — thread-safe per-process, double-checked locking
  2. AsyncLazySingleton — per-task isolation via ContextVar
  3. AsyncLazySingleton does NOT share instance across async tasks
  4. reset() works on both variants

Invariant table:
| Test                    | Validates                                  |
|-------------------------|--------------------------------------------|
| test_sync_singleton     | LazySingleton: same instance, DCLP         |
| test_sync_concurrent     | LazySingleton: 100 threads → 1 instance    |
| test_sync_reset         | LazySingleton.reset() clears state         |
| test_async_singleton     | AsyncLazySingleton: same task → same inst  |
| test_async_different_tasks | AsyncLazySingleton: different tasks → diff |
| test_async_reset        | AsyncLazySingleton.reset() clears state    |
| test_async_lock_is_task_local | asyncio.Lock created per task via ALS |
| test_async_queue_is_task_local | asyncio.Queue created per task via ALS |
"""


import asyncio
import threading
import time

import pytest

from utils.lazy_singleton import AsyncLazySingleton, LazySingleton


# ---------------------------------------------------------------------------
# Sync LazySingleton tests
# ---------------------------------------------------------------------------


def test_sync_singleton() -> None:
    """LazySingleton returns the same instance on every call."""
    factory_calls: int = 0

    def make_value() -> dict[str, int]:
        nonlocal factory_calls
        factory_calls += 1
        return {"id": factory_calls}

    singleton = LazySingleton(make_value)

    results = [singleton() for _ in range(10)]
    assert factory_calls == 1, "factory called once"
    assert all(r is results[0] for r in results), "same instance returned"


def test_sync_reset() -> None:
    """reset() clears the cached instance; next call re-creates it."""
    counter: int = 0

    def factory() -> list[str]:
        nonlocal counter
        counter += 1
        return [f"v{counter}"]

    s = LazySingleton(factory)
    assert s() == ["v1"]
    assert s() == ["v1"]
    s.reset()
    assert s() == ["v2"]
    assert counter == 2


def test_sync_concurrent() -> None:
    """100 threads racing to create the singleton → exactly 1 factory call."""
    factory_calls: int = 0
    lock = threading.Lock()

    def make_value() -> tuple[int, float]:
        with lock:
            nonlocal factory_calls
            factory_calls += 1
        time.sleep(0.05)  # simulate slow init
        return (factory_calls, time.time())

    singleton = LazySingleton(make_value)
    results: list[tuple[int, float]] = []
    barrier = threading.Barrier(100)

    def runner() -> None:
        barrier.wait()  # synchronize start
        results.append(singleton())

    threads = [threading.Thread(target=runner) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert factory_calls == 1, f"expected 1 factory call, got {factory_calls}"
    assert len(set(id(r) for r in results)) == 1, "all threads got same instance"


# ---------------------------------------------------------------------------
# AsyncLazySingleton tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_singleton() -> None:
    """Same async task → same instance."""
    counter: int = 0

    def factory() -> asyncio.Lock:
        nonlocal counter
        counter += 1
        return asyncio.Lock()

    singleton = AsyncLazySingleton(factory)
    lock1 = singleton()
    lock2 = singleton()
    assert lock1 is lock2, "same task got same instance"
    assert counter == 1, "factory called once"


@pytest.mark.asyncio
async def test_async_different_tasks_get_different_instances() -> None:
    """Different async tasks → different instances (per ContextVar)."""
    seen_ids: set[int] = set()
    barrier = asyncio.Barrier(5)

    def factory() -> asyncio.Lock:
        return asyncio.Lock()

    singleton = AsyncLazySingleton(factory)

    async def worker() -> None:
        await barrier.wait()  # synchronize
        lock = singleton()
        seen_ids.add(id(lock))

    await asyncio.gather(*[worker() for _ in range(5)])
    assert len(seen_ids) == 5, f"expected 5 distinct locks, got {len(seen_ids)}"


@pytest.mark.asyncio
async def test_async_reset() -> None:
    """reset() clears the ContextVar; next call re-creates the instance."""
    counter: int = 0

    def factory() -> list[str]:
        nonlocal counter
        counter += 1
        return [f"v{counter}"]

    s = AsyncLazySingleton(factory)
    assert s() == ["v1"]
    assert s() == ["v1"]
    s.reset()
    assert s() == ["v2"]
    assert counter == 2


@pytest.mark.asyncio
async def test_async_lock_is_task_local() -> None:
    """AsyncLazySingleton[asyncio.Lock] gives each task its own lock."""
    stored_ids: list[int] = []

    def make_lock() -> asyncio.Lock:
        return asyncio.Lock()

    singleton = AsyncLazySingleton(make_lock)

    async def task_a() -> None:
        lock = singleton()
        stored_ids.append(id(lock))
        await asyncio.sleep(0.05)  # let task_b run
        stored_ids.append(id(singleton()))  # same task → same lock

    async def task_b() -> None:
        await asyncio.sleep(0.02)
        lock = singleton()
        stored_ids.append(id(lock))

    await asyncio.gather(task_a(), task_b())
    # [lock_a, lock_a_same, lock_b] → 2 unique
    assert len(set(stored_ids)) == 2, f"expected 2 distinct locks, got {stored_ids}"


@pytest.mark.asyncio
async def test_async_queue_is_task_local() -> None:
    """AsyncLazySingleton[asyncio.Queue] gives each task its own queue."""
    seen: list[int] = []

    def make_queue() -> asyncio.Queue[str]:
        return asyncio.Queue()

    singleton = AsyncLazySingleton(make_queue)
    barrier = asyncio.Barrier(3)

    async def putter(_name: str) -> None:
        q = singleton()
        await barrier.wait()
        seen.append(id(q))

    await asyncio.gather(putter("a"), putter("b"), putter("c"))
    assert len(set(seen)) == 3, f"expected 3 distinct queues, got {len(set(seen))}"


@pytest.mark.asyncio
async def test_async_nested_tasks_share_context() -> None:
    """create_task() inherits ContextVar — child gets same instance as parent.

    This is correct asyncio behaviour: ContextVar is task-local, and
    create_task() runs the child in the same ContextVar context as the parent.
    The real loop-boundary problem occurs with nested asyncio.run() calls
    (separate event loops), not with create_task() within one loop.
    """
    outer_ids: list[int] = []
    inner_ids: list[int] = []

    def make_lock() -> asyncio.Lock:
        return asyncio.Lock()

    singleton = AsyncLazySingleton(make_lock)

    async def outer() -> None:
        outer_lock = singleton()
        outer_ids.append(id(outer_lock))
        inner_task = asyncio.create_task(inner())
        await inner_task

    async def inner() -> None:
        inner_lock = singleton()
        inner_ids.append(id(inner_lock))

    await outer()
    # Same loop + create_task = same ContextVar context → same lock instance
    assert len(set(outer_ids)) == 1
    assert len(set(inner_ids)) == 1
    assert outer_ids[0] == inner_ids[0], (
        f"create_task inherits ContextVar — outer and inner must share the lock"
    )


def test_async_runs_isolation() -> None:
    """Separate asyncio.run() calls get different instances (separate loops).

    This is the actual problem AsyncLazySingleton solves: if you call
    asyncio.run() twice (nested), each loop creates its own ContextVar copy,
    so each gets its own instance.
    """
    outer_ids: list[int] = []
    inner_ids: list[int] = []

    def make_lock() -> asyncio.Lock:
        return asyncio.Lock()

    singleton = AsyncLazySingleton(make_lock)

    async def runner(ids: list[int]) -> None:
        lock = singleton()
        ids.append(id(lock))

    # Two separate asyncio.run() = two separate event loops
    asyncio.run(runner(outer_ids))
    asyncio.run(runner(inner_ids))

    assert outer_ids[0] != inner_ids[0], (
        f"separate asyncio.run() calls must get different instances: "
        f"outer={outer_ids[0]}, inner={inner_ids[0]}"
    )
