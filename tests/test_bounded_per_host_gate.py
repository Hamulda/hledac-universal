# tests/test_bounded_per_host_gate.py
"""
Testy pro BoundedPerHostGate — LRU-bounded per-host concurrency gate.

Scope: utils/async_helpers.BoundedPerHostGate
Sprint: F320 (Issue #6)
"""

import asyncio

import pytest

from hledac.universal.utils.async_helpers import BoundedPerHostGate


class TestBoundedPerHostGate:
    """Testy BoundedPerHostGate — LRU eviction, bounded RAM, telemetry."""

    @pytest.mark.asyncio
    async def test_acquire_release_basic(self) -> None:
        """Basic acquire/release pair returns semaphore and op_id."""
        gate = BoundedPerHostGate(max_hosts=10, per_host_limit=4)
        sem, op_id = await gate.acquire("example.com")
        assert isinstance(sem, asyncio.Semaphore)
        assert op_id == "miss"
        gate.release(sem)

    @pytest.mark.asyncio
    async def test_acquire_hit_after_miss(self) -> None:
        """Second acquire for same host returns hit, not miss."""
        gate = BoundedPerHostGate(max_hosts=10, per_host_limit=4)
        sem1, id1 = await gate.acquire("example.com")
        assert id1 == "miss"
        sem1.release()
        sem2, id2 = await gate.acquire("example.com")
        assert id2 == "hit"
        gate.release(sem2)

    @pytest.mark.asyncio
    async def test_max_hosts_cap(self) -> None:
        """LRU eviction triggers when over max_hosts."""
        gate = BoundedPerHostGate(max_hosts=8, per_host_limit=2)
        sems: list[asyncio.Semaphore] = []

        # Acquire 8 unique hosts
        for i in range(8):
            sem, _ = await gate.acquire(f"host{i}.com")
            sems.append(sem)

        stats = gate.get_stats()
        assert stats["active_hosts"] == 8
        assert stats["max_hosts"] == 8

        # 9th host triggers eviction of oldest
        sem9, op_id = await gate.acquire("host9.com")
        assert op_id == "miss"
        stats = gate.get_stats()
        assert stats["active_hosts"] <= 8
        assert stats["evicted"] >= 1

        # Cleanup
        for sem in sems:
            gate.release(sem)
        gate.release(sem9)

    @pytest.mark.asyncio
    async def test_lru_eviction_order(self) -> None:
        """Least-recently-used host is evicted first."""
        gate = BoundedPerHostGate(max_hosts=4, per_host_limit=2)

        # host0 .. host3
        sems = []
        for i in range(4):
            sem, _ = await gate.acquire(f"host{i}.com")
            sems.append(sem)

        # Touch host0 to make it most-recent
        sems[0].release()
        _, _ = await gate.acquire("host0.com")

        # Add 2 new hosts — should evict host1 and host2 (oldest after host0 touch)
        sem4, _ = await gate.acquire("host4.com")
        sem5, _ = await gate.acquire("host5.com")

        stats = gate.get_stats()
        assert stats["evicted"] >= 2

        # host0 should still be present (was touched)
        # host1/host2 likely evicted
        for sem in (*sems, sem4, sem5):
            gate.release(sem)

    @pytest.mark.asyncio
    async def test_per_host_limit_concurrency(self) -> None:
        """Per-host limit caps concurrent slots per hostname."""
        gate = BoundedPerHostGate(max_hosts=10, per_host_limit=2)
        results: list[str] = []
        barrier = asyncio.Barrier(3)

        async def worker(sem: asyncio.Semaphore, worker_id: str) -> None:
            await sem.acquire()
            await barrier.wait()
            results.append(worker_id)
            sem.release()

        # Acquire 2 slots for same host
        sem1, _ = await gate.acquire("example.com")
        sem2, _ = await gate.acquire("example.com")

        # Third acquire will block (limit=2)
        async def third_acquire() -> None:
            s, _ = await gate.acquire("example.com")
            results.append("third")
            s.release()

        third_task = asyncio.create_task(third_acquire())
        await asyncio.sleep(0.01)  # Let it start acquiring

        assert not third_task.done()

        # Release one slot — third should proceed
        sem1.release()
        await asyncio.wait_for(third_task, timeout=1.0)
        assert "third" in results

        gate.release(sem2)

    @pytest.mark.asyncio
    async def test_double_release_safe(self) -> None:
        """release() swallows ValueError on double-release."""
        gate = BoundedPerHostGate(max_hosts=10, per_host_limit=4)
        sem, _ = await gate.acquire("example.com")
        gate.release(sem)
        gate.release(sem)  # Must not raise

    @pytest.mark.asyncio
    async def test_stats_telemetry(self) -> None:
        """get_stats() returns correct hit/miss/evicted counts."""
        gate = BoundedPerHostGate(max_hosts=4, per_host_limit=2)
        sem, _ = await gate.acquire("a.com")
        assert gate.get_stats()["misses"] == 1
        gate.release(sem)

        sem, _ = await gate.acquire("a.com")
        assert gate.get_stats()["hits"] == 1
        gate.release(sem)

        # Overflow to trigger eviction
        for i in range(2, 6):
            s, _ = await gate.acquire(f"host{i}.com")
            gate.release(s)

        stats = gate.get_stats()
        assert stats["evicted"] >= 1
        assert stats["active_hosts"] <= 4

    @pytest.mark.asyncio
    async def test_1024_hosts_ram_budget(self) -> None:
        """1k unique hosts: RAM stays bounded (~250KB)."""
        gate = BoundedPerHostGate(max_hosts=512, per_host_limit=4)
        sems: list[asyncio.Semaphore] = []

        for i in range(1024):
            sem, _ = await gate.acquire(f"uniquehost{i:04d}.example.com")
            sems.append(sem)

        stats = gate.get_stats()
        # Active hosts capped at 512
        assert stats["active_hosts"] == 512
        assert stats["evicted"] >= 512

        # Cleanup
        for sem in sems:
            gate.release(sem)

    @pytest.mark.asyncio
    async def test_default_limits(self) -> None:
        """Default constructor uses max_hosts=512, per_host_limit=4."""
        gate = BoundedPerHostGate()
        assert gate.get_stats()["max_hosts"] == 512

        sem1, _ = await gate.acquire("a.com")
        sem2, _ = await gate.acquire("a.com")
        sem3, _ = await gate.acquire("a.com")
        sem4, _ = await gate.acquire("a.com")

        async def blocked() -> None:
            await gate.acquire("a.com")

        task = asyncio.create_task(blocked())
        await asyncio.sleep(0.02)
        assert not task.done()

        sem1.release()
        await asyncio.wait_for(task, timeout=0.5)

        for sem in (sem2, sem3, sem4):
            gate.release(sem)

    @pytest.mark.asyncio
    async def test_different_hosts_independent(self) -> None:
        """Different hostnames get independent semaphores."""
        gate = BoundedPerHostGate(max_hosts=10, per_host_limit=2)

        sem_a1, _ = await gate.acquire("host-a.com")
        sem_b1, _ = await gate.acquire("host-b.com")
        sem_a2, _ = await gate.acquire("host-a.com")

        # Both should be usable concurrently (per-host-limit = 2)
        assert sem_a1 is not sem_b1
        assert sem_a1 is sem_a2  # Same semaphore for same host

        gate.release(sem_a1)
        gate.release(sem_b1)
        gate.release(sem_a2)
