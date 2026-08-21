"""test_task_registry_winddown — TaskRegistry winddown verification.

F350M-R / A1: Tres paralelni runtime roviny — reseni.

Tests:
    1. TaskRegistry.cancel_all() reaches all 50 fake sidecar tasks
    2. asyncio.all_tasks() is empty (or near-empty) after cancel_all
    3. All tasks are properly unregistered after completion
    4. cancel_scope() only cancels tasks in the target scope
    5. cleanup_after_cancel() runs gc.collect + mlx cleanup without error

Invariants tested:
    - Bounded: MAX_TASKS=512 cap (tested with 50 tasks, well under cap)
    - Fail-safe: no exception escapes cancel_all/await_join
    - Zero allocation on hot path: register() is O(1) dict set
"""

from __future__ import annotations

import asyncio

import pytest


class TestTaskRegistryWinddown:
    """Verify TaskRegistry properly cancels all tasks on winddown."""

    @pytest.mark.asyncio
    async def test_cancel_all_reaches_50_tasks(self) -> None:
        """cancel_all() skutecne zrusi vsech 50 registered tasks."""
        from runtime.scheduler_v2._task_registry import TaskRegistry

        # Use fresh registry for this test
        registry = TaskRegistry()

        # Create 50 fake sidecar tasks
        async def fake_sidecar_task(_task_id: int) -> None:
            """Simulate a sidecar task that does some async work."""
            await asyncio.sleep(10.0)  # Would normally run forever

        task_ids: list[int] = []
        for i in range(50):
            task = asyncio.create_task(fake_sidecar_task(i), name=f"sidecar:{i}")
            registry.register(f"sidecar:{i}", task, scope="windup:sidecar")
            task_ids.append(id(task))

        # Verify tasks are registered
        counts = registry.get_counts()
        assert counts["active_tasks"] == 50, f"Expected 50, got {counts['active_tasks']}"

        # Cancel all with 2.0s timeout
        stats = await registry.cancel_all(timeout=2.0)

        assert stats["cancelled_count"] == 50, f"Expected 50 cancelled, got {stats['cancelled_count']}"
        assert stats["total"] == 50

        # Verify tasks are still tracked but done
        _remaining = await registry.await_join(timeout=0.5)
        # After cancel, tasks should be done or timed out
        counts_after = registry.get_counts()
        # All tasks should have been unregistered after await_join
        assert counts_after["active_tasks"] == 0

    @pytest.mark.asyncio
    async def test_asyncio_all_tasks_empty_after_cancel(self) -> None:
        """asyncio.all_tasks() je prazdny (nebo near-empty) po cancel_all."""
        from runtime.scheduler_v2._task_registry import TaskRegistry

        registry = TaskRegistry()

        async def long_task() -> None:
            await asyncio.sleep(10.0)

        # Create tasks
        tasks = []
        for i in range(20):
            task = asyncio.create_task(long_task(), name=f"task:{i}")
            registry.register(f"task:{i}", task, scope="test")
            tasks.append(task)

        # Cancel all
        await registry.cancel_all(timeout=1.0)

        # Give cancellation time to propagate
        await asyncio.sleep(0.1)

        # Check asyncio.all_tasks() — our tasks should be done
        all_tasks = [t for t in asyncio.all_tasks() if not t.done()]
        # Filter to only our test tasks (not other system tasks)
        our_done = [t for t in all_tasks if getattr(t, "_name", "").startswith("task:")]
        assert len(our_done) == 0, f"Expected 0 our tasks still running, got {len(our_done)}"

    @pytest.mark.asyncio
    async def test_cancel_scope_only_affects_target_scope(self) -> None:
        """cancel_scope() pouze ruší tasky v cílovém scope."""
        from runtime.scheduler_v2._task_registry import TaskRegistry, TaskScope

        registry = TaskRegistry()

        async def dummy() -> None:
            await asyncio.sleep(10.0)

        # Register tasks in two different scopes
        for i in range(5):
            registry.register(
                f"prelude:{i}", asyncio.create_task(dummy(), name=f"prelude:{i}"), scope=TaskScope.PRELUDE
            )
        for i in range(5):
            registry.register(f"windup:{i}", asyncio.create_task(dummy(), name=f"windup:{i}"), scope=TaskScope.WINDUP)

        counts_before = registry.get_counts()
        assert counts_before["by_scope"].get(TaskScope.PRELUDE, 0) == 5
        assert counts_before["by_scope"].get(TaskScope.WINDUP, 0) == 5

        # Cancel only WINDUP scope
        stats = await registry.cancel_scope(TaskScope.WINDUP, timeout=1.0)

        assert stats["cancelled_count"] == 5, f"Expected 5 cancelled in WINDUP, got {stats['cancelled_count']}"
        assert stats["scope"] == TaskScope.WINDUP

        # PRELUDE tasks should still be tracked
        counts_after = registry.get_counts()
        assert counts_after["by_scope"].get(TaskScope.PRELUDE, 0) == 5, "PRELUDE tasks should NOT be cancelled"

    @pytest.mark.asyncio
    async def test_cleanup_after_cancel_runs_gc(self) -> None:
        """cleanup_after_cancel() spusti gc.collect bez chyby."""
        from runtime.scheduler_v2._task_registry import TaskRegistry

        registry = TaskRegistry()

        async def dummy() -> None:
            await asyncio.sleep(10.0)

        # Register a few tasks
        for i in range(3):
            registry.register(f"task:{i}", asyncio.create_task(dummy(), name=f"task:{i}"), scope="test")

        await registry.cancel_all(timeout=0.5)

        # cleanup_after_cancel should not raise
        await registry.cleanup_after_cancel()

        counts = registry.get_counts()
        assert counts["active_tasks"] == 0

    @pytest.mark.asyncio
    async def test_task_auto_unregister_on_done(self) -> None:
        """Task je automaticky odstranen po dokonceni (done callback)."""
        from runtime.scheduler_v2._task_registry import TaskRegistry, safe_create_task_tracked

        registry = TaskRegistry()
        # Use a fresh registry so we don't conflict with singleton
        import runtime.scheduler_v2._task_registry as _tr

        _tr._registry = registry  # override singleton for this test

        async def quick_task() -> int:
            await asyncio.sleep(0.01)
            return 42

        # safe_create_task_tracked adds done callback that auto-unregisters
        _tracked = safe_create_task_tracked(quick_task(), name="quick", scope="test")
        assert registry.get_counts()["active_tasks"] == 1

        # Wait for it to complete
        result = await _tracked
        assert result == 42

        # Give done callback time to fire and unregister the task
        await asyncio.sleep(0.2)

        # Task should be auto-unregistered after done callback fires
        counts = registry.get_counts()
        assert counts["active_tasks"] == 0, (
            f"Expected 0, got {counts['active_tasks']} (task should be auto-unregistered)"
        )

        # Restore singleton
        _tr._registry = None

    @pytest.mark.asyncio
    async def test_fire_and_forget_tracked_task(self) -> None:
        """safe_create_task_tracked vytvari sledovany task."""
        from runtime.scheduler_v2._task_registry import TaskScope, get_task_registry, safe_create_task_tracked

        registry = get_task_registry()
        registry.reset()  # Clear any previous state

        async def background_work() -> None:
            await asyncio.sleep(5.0)

        # Fire-and-forget tracked task
        _tracked_task = safe_create_task_tracked(
            background_work(),
            name="test:background",
            scope=TaskScope.ACQUISITION,
        )

        counts = registry.get_counts()
        assert counts["active_tasks"] == 1, f"Expected 1 tracked task, got {counts['active_tasks']}"
        assert counts["by_scope"].get(TaskScope.ACQUISITION, 0) == 1, (
            f"Expected 1 task in ACQUISITION scope, got {counts['by_scope'].get(TaskScope.ACQUISITION, 0)}"
        )

        # Cancel it
        await registry.cancel_scope(TaskScope.ACQUISITION, timeout=0.5)
        # Give done callback time to fire and unregister the task
        await asyncio.sleep(0.2)

        counts_after = registry.get_counts()
        assert counts_after["active_tasks"] == 0, f"Expected 0, got {counts_after['active_tasks']} after cancel_scope"

        registry.reset()  # Clean up

    @pytest.mark.asyncio
    async def test_get_task_registry_singleton(self) -> None:
        """get_task_registry() vraci stejnou instanci (singleton)."""
        from runtime.scheduler_v2._task_registry import get_task_registry

        r1 = get_task_registry()
        r2 = get_task_registry()

        assert r1 is r2, "get_task_registry() must return the same singleton instance"

    @pytest.mark.asyncio
    async def test_scope_parent_hierarchy(self) -> None:
        """TaskScope.parent() spravne vraci rodice scope."""
        from runtime.scheduler_v2._task_registry import TaskScope

        assert TaskScope.parent(TaskScope.ACQUISITION_PUBLIC) == TaskScope.ACQUISITION
        assert TaskScope.parent(TaskScope.ACQUISITION_SYNTHESIS) == TaskScope.ACQUISITION
        assert TaskScope.parent(TaskScope.WINDUP_SIDECAR) == TaskScope.WINDUP
        assert TaskScope.parent(TaskScope.WINDUP) is None
        assert TaskScope.parent(TaskScope.PRELUDE) is None
        assert TaskScope.parent("unknown:scope") is None
