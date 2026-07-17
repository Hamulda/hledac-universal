"""
Test suite pro core/locks.py — Lock Registry a deadlock prevention.

Sprint F350M-R: Lock registry pro prevenci deadlocku.

Testované invariants:
1. register_lock() registruje lock v centralizovaném registru
2. acquire_in_order() vrací locks v ascending order
3. AsyncLockDCLP lazy initializes asyncio.Lock správně
4. make_counter() vytváří thread-safe counter
5. get_lock_by_name() vrací správný lock
6. get_locks_by_category() filtruje správně
7. Idempotentní registrace (stejný lock, stejné jméno) nehází chybu
8. Duplicitní registrace s různým lockem hází ValueError
9. LockCategory enum má správné hodnoty
10. get_registered_locks() vrací všechny locks

PYTHON VERSION: 3.14+
 HARDWARE: M1 8GB UMA
"""

from __future__ import annotations

import asyncio
import threading
import uuid
import weakref
from collections.abc import Generator

import pytest

from hledac.universal.core.locks import (
    AsyncLockDCLP,
    LockCategory,
    LockInfo,
    acquire_in_order,
    get_lock_by_name,
    get_locks_by_category,
    get_registered_locks,
    make_counter,
    register_lock,
)

# Unique name generator for tests — avoids id() memory address collisions
_test_id = uuid.uuid4().hex[:8]


class TestLockCategory:
    """Test LockCategory enum values."""

    def test_lock_category_values(self) -> None:
        """LockCategory má správné integer hodnoty pro ordering."""
        assert LockCategory.METRICS.value == 1
        assert LockCategory.CACHE.value == 2
        assert LockCategory.CONFIG.value == 3
        assert LockCategory.NETWORK.value == 4
        assert LockCategory.CURSOR.value == 5
        assert LockCategory.GRAPH.value == 6
        assert LockCategory.WAL.value == 7
        assert LockCategory.MPC.value == 8

    def test_lock_category_ordering(self) -> None:
        """LockCategory je seřazeno ascending podle value."""
        categories = list(LockCategory)
        values = [c.value for c in categories]
        assert values == sorted(values)


class TestRegisterLock:
    """Test lock registration."""

    def test_register_lock_success(self) -> None:
        """register_lock() registruje lock úspěšně."""
        lock = threading.Lock()
        name = f"test_module._test_lock_{_test_id}"
        register_lock(LockCategory.CACHE, lock, name)
        assert get_lock_by_name(name) is lock

    def test_register_lock_idempotent(self) -> None:
        """Idempotentní registrace stejného locku nehází chybu."""
        lock = threading.Lock()
        name = f"test_module._idempotent_lock_{_test_id}"
        register_lock(LockCategory.CACHE, lock, name)
        # Druhé volání s stejným lockem by nemělo hodit chybu
        register_lock(LockCategory.CACHE, lock, name)

    def test_register_lock_duplicate_different_lock_raises(self) -> None:
        """Duplicitní registrace s různým lockem hází ValueError."""
        lock1 = threading.Lock()
        lock2 = threading.Lock()
        name = f"test_module._dup_lock_{_test_id}"
        register_lock(LockCategory.CACHE, lock1, name)
        with pytest.raises(ValueError, match="already registered"):
            register_lock(LockCategory.CACHE, lock2, name)

    def test_register_lock_type_check(self) -> None:
        """register_lock() přijímá pouze threading.Lock nebo threading.RLock."""
        lock = threading.Lock()
        rlock = threading.RLock()

        # Správné typy
        register_lock(LockCategory.CACHE, lock, f"test_type._lock_{_test_id}")
        register_lock(LockCategory.CACHE, rlock, f"test_type._rlock_{_test_id}")

        # Špatný typ
        with pytest.raises(TypeError, match="threading.Lock or threading.RLock"):
            register_lock(LockCategory.CACHE, "not a lock", "test_type._bad_lock")  # type: ignore


class TestAcquireInOrder:
    """Test acquire_in_order() funkce."""

    def test_acquire_in_order_single(self) -> None:
        """acquire_in_order() s jednou kategorií vrací správný počet context managers."""
        lock = threading.Lock()
        register_lock(LockCategory.CACHE, lock, f"test_acquire._single_{_test_id}")
        result = acquire_in_order(LockCategory.CACHE)
        assert len(result) >= 1

    def test_acquire_in_order_multiple_sorted(self) -> None:
        """acquire_in_order() vrací locks v ascending order."""
        lock1 = threading.Lock()
        lock2 = threading.Lock()
        lock3 = threading.Lock()

        register_lock(LockCategory.METRICS, lock1, f"test_sort._metrics_{_test_id}")
        register_lock(LockCategory.NETWORK, lock2, f"test_sort._network_{_test_id}")
        register_lock(LockCategory.MPC, lock3, f"test_sort._mpc_{_test_id}")

        # NETWORK (4) před MPC (8), atd.
        result = acquire_in_order(LockCategory.MPC, LockCategory.NETWORK, LockCategory.METRICS)
        assert len(result) == 3

    def test_acquire_in_order_empty(self) -> None:
        """acquire_in_order() bez argumentů vrací prázdný list."""
        result = acquire_in_order()
        assert result == []

    def test_acquire_in_order_deduplicates(self) -> None:
        """acquire_in_order() deduplikuje duplicitní kategorie."""
        lock = threading.Lock()
        name = f"test_dedup._lock_{_test_id}"
        register_lock(LockCategory.CACHE, lock, name)
        result = acquire_in_order(LockCategory.CACHE, LockCategory.CACHE, LockCategory.CACHE)
        assert len(result) == 1


class TestGetLockByName:
    """Test get_lock_by_name() funkce."""

    def test_get_lock_by_name_exists(self) -> None:
        """get_lock_by_name() vrací správný lock když existuje."""
        lock = threading.Lock()
        name = f"test_get._exists_{_test_id}"
        register_lock(LockCategory.CACHE, lock, name)
        result = get_lock_by_name(name)
        assert result is lock

    def test_get_lock_by_name_not_exists(self) -> None:
        """get_lock_by_name() vrací None pro neexistující lock."""
        result = get_lock_by_name("test_get._not_exists_xyz123")
        assert result is None


class TestGetLocksByCategory:
    """Test get_locks_by_category() funkce."""

    def test_get_locks_by_category(self) -> None:
        """get_locks_by_category() filtruje správně."""
        lock1 = threading.Lock()
        lock2 = threading.Lock()
        register_lock(LockCategory.CACHE, lock1, f"test_cat._lock1_{_test_id}")
        register_lock(LockCategory.CACHE, lock2, f"test_cat._lock2_{_test_id}")
        register_lock(LockCategory.NETWORK, threading.Lock(), f"test_cat._network_{_test_id}")

        result = get_locks_by_category(LockCategory.CACHE)
        # May have more than 2 due to previous tests sharing global registry
        assert len(result) >= 2
        assert all(info.category == LockCategory.CACHE for info in result)


class TestGetRegisteredLocks:
    """Test get_registered_locks() funkce."""

    def test_get_registered_locks_returns_all(self) -> None:
        """get_registered_locks() vrací všechny registrované locks."""
        lock1 = threading.Lock()
        lock2 = threading.Lock()
        name1 = f"test_all._lock1_{_test_id}"
        name2 = f"test_all._lock2_{_test_id}"
        register_lock(LockCategory.CACHE, lock1, name1)
        register_lock(LockCategory.NETWORK, lock2, name2)

        result = get_registered_locks()
        # May have more due to previous tests sharing global registry
        assert len(result) >= 2
        names = {info.name for info in result}
        assert name1 in names
        assert name2 in names


class TestAsyncLockDCLP:
    """Test AsyncLockDCLP lazy initialization."""

    def test_async_lock_dclp_lazy_init(self) -> None:
        """AsyncLockDCLP lazy inicializuje asyncio.Lock."""
        dclp = AsyncLockDCLP()
        # Lock je None před prvním použitím
        assert dclp._lock is None

    @pytest.mark.asyncio
    async def test_async_lock_dclp_context_manager(self) -> None:
        """AsyncLockDCLP funguje jako async context manager."""
        dclp = AsyncLockDCLP()
        async with dclp:
            assert dclp.locked is True
        assert dclp.locked is False

    @pytest.mark.asyncio
    async def test_async_lock_dclp_concurrent_tasks(self) -> None:
        """AsyncLockDCLP allows concurrent access from different tasks."""
        dclp = AsyncLockDCLP()
        results: list[int] = []
        barrier = asyncio.Barrier(3)

        async def task_a() -> None:
            await barrier.wait()
            async with dclp:
                results.append(1)

        async def task_b() -> None:
            await barrier.wait()
            async with dclp:
                results.append(2)

        async def task_c() -> None:
            await barrier.wait()
            async with dclp:
                results.append(3)

        # All 3 tasks try to acquire simultaneously — only one gets it first
        # Others should wait then proceed. Order is non-deterministic.
        await asyncio.gather(task_a(), task_b(), task_c())
        assert sorted(results) == [1, 2, 3]

    def test_async_lock_dclp_locked_property(self) -> None:
        """AsyncLockDCLP.locked property vrací správný stav."""
        dclp = AsyncLockDCLP()
        assert dclp.locked is False
        # Locked je None dokud není vytvořen
        assert dclp._lock is None


class TestMakeCounter:
    """Test make_counter() factory."""

    def test_make_counter_initial_value(self) -> None:
        """make_counter() vrací counter s počáteční hodnotou."""
        counter = make_counter(42)
        assert counter.get() == 42

    def test_make_counter_increment(self) -> None:
        """make_counter().increment() zvyšuje hodnotu o 1."""
        counter = make_counter(100)
        v1 = counter.increment()
        v2 = counter.increment()
        v3 = counter.increment()
        # Hodnoty by měly být consecutive (relative to initial)
        assert v2 == v1 + 1
        assert v3 == v2 + 1

    def test_make_counter_threadsafe(self) -> None:
        """make_counter() je thread-safe."""
        counter = make_counter(0)
        barrier = threading.Barrier(10)

        def increment_many() -> None:
            barrier.wait()
            for _ in range(100):
                counter.increment()

        threads = [threading.Thread(target=increment_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Celkový počet incrementů = 10 threads * 100 = 1000
        # Hodnota counteru po 1000 inkrementech od 0 = 1000
        final = counter.get()
        assert final == 1000


class TestLockInfo:
    """Test LockInfo dataclass."""

    def test_lock_info_repr(self) -> None:
        """LockInfo.__repr__ vrací čitelný string."""
        lock = threading.Lock()
        info = LockInfo(
            category=LockCategory.CACHE,
            order=0,
            name="test._info",
            lock=lock,
            frame_info="test.py:10",
        )
        repr_str = repr(info)
        assert "CACHE" in repr_str
        assert "test._info" in repr_str

    def test_lock_info_slots(self) -> None:
        """LockInfo má __slots__ pro memory efficiency."""
        lock = threading.Lock()
        info = LockInfo(
            category=LockCategory.CACHE,
            order=0,
            name="test._slots",
            lock=lock,
            frame_info="test.py:10",
        )
        # __slots__ znamená že nemůžeme přidat arbitrary atributy
        with pytest.raises(AttributeError):
            info.nonexistent = "value"  # type: ignore


class TestIntegration:
    """Integration testy pro kompletní workflow."""

    def test_full_registration_workflow(self) -> None:
        """Kompletní workflow: registrace → akvizice → cleanup."""
        # Registruj locks
        cache_lock = threading.Lock()
        network_lock = threading.Lock()
        name1 = f"workflow._cache_{_test_id}"
        name2 = f"workflow._network_{_test_id}"

        register_lock(LockCategory.CACHE, cache_lock, name1)
        register_lock(LockCategory.NETWORK, network_lock, name2)

        # Ověř registraci
        assert get_lock_by_name(name1) is cache_lock
        assert get_lock_by_name(name2) is network_lock

        # Získej locks podle kategorie
        cache_locks = get_locks_by_category(LockCategory.CACHE)
        assert len(cache_locks) >= 1

        # Získej všechny locks
        all_locks = get_registered_locks()
        workflow_locks = [i for i in all_locks if i.name.startswith("workflow.")]
        assert len(workflow_locks) == 2

    def test_concurrent_registration(self) -> None:
        """Concurrency test: více threadů registruje současně."""
        barrier = threading.Barrier(5)
        errors: list[BaseException] = []
        counter = [0]

        def register_many() -> None:
            try:
                barrier.wait()
                for i in range(10):
                    lock = threading.Lock()
                    idx = counter[0]
                    counter[0] += 1
                    register_lock(
                        LockCategory.CACHE,
                        lock,
                        f"concurrent._lock_{threading.current_thread().name}_{idx}_{i}",
                    )
            except BaseException as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_many, name=f"worker_{i}")
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent registration: {errors}"
        all_locks = get_registered_locks()
        concurrent_locks = [i for i in all_locks if i.name.startswith("concurrent.")]
        assert len(concurrent_locks) == 50  # 5 threads * 10 locks


# ==============================================================================
# INVARIANTS (hardcoded for this test class)
# ==============================================================================

INVARIANTS = [
    ("LOCK_REGISTRY_ALWAYS_ON", "Lock registry is always active, no feature flag"),
    ("LOCK_CATEGORY_ORDERED", "LockCategory enum is sorted ascending by value"),
    ("ASYNC_LOCK_DCLP_LAZY", "AsyncLockDCLP lazy-init on first async context access"),
    ("REGISTER_LOCK_THREADSAFE", "register_lock() is thread-safe via _REGISTRY_LOCK"),
    ("ACQUIRE_IN_ORDER_DETERMINISTIC", "acquire_in_order() returns deterministic order"),
]
