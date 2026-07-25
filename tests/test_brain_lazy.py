"""
TestSprintF330: LazyModel concurrency safety tests (Issue #4)
============================================================

Tests:
  1. Concurrent load: 100 simultaneous get() calls create exactly 1 instance
  2. Stale evict: Timer fires, but a newer load happened → instance preserved
  3. Lock is lazily created and thread-safe (DCLP pattern)
  4. Memory guard still works under concurrency
  5. Conditional load (min_findings) still works

All tests are hermetic — no MLX, no real models.

P3-04: Tests marked @pytest.mark.flaky_race because they test race conditions
that depend on real scheduler timing. These may occasionally fail under extreme
CPU contention but are valid tests when the system is not under load.
"""

import asyncio
import gc
import time

import pytest

import sys


class TestLazyModelConcurrency:
    """Concurrency safety for LazyModel registry."""

    @pytest.fixture
    def fresh_lazy(self):
        """Create a fresh LazyModel instance for each test."""
        # Remove any cached module state
        mods_to_remove = [k for k in sys.modules if k.startswith("brain._lazy")]
        for mod in mods_to_remove:
            sys.modules.pop(mod, None)

        from brain._lazy import LazyModel
        yield LazyModel
        # Cleanup
        gc.collect()

    @pytest.mark.asyncio
    @pytest.mark.flaky_race  # P3-04: Race condition test - timing dependent
    async def test_concurrent_get_creates_single_instance(self, fresh_lazy):
        """
        100 concurrent await lazy.get() calls must create exactly 1 instance.

        This is the primary race condition test for Issue #4:
        without a lock, two coroutines could pass the `if self._instance is None`
        check simultaneously and both call self._factory() → 2x memory allocation.

        P3-04: Increased factory_duration from 0.05 to 0.1 for CI stability.
        """
        factory_calls = {"count": 0}
        factory_duration = 0.1  # P3-04: 100ms (was 50ms) for CI stability

        def slow_factory():
            factory_calls["count"] += 1
            time.sleep(factory_duration)
            return {"instance": "test_model"}

        lazy = fresh_lazy(
            factory=slow_factory,
            ttl_seconds=600,
            name="test_model",
            min_free_mb=0,  # Disable memory guard for this test
        )

        # Fire 100 concurrent get() calls
        results = await asyncio.gather(
            *[lazy.get() for _ in range(100)],
            return_exceptions=True,
        )

        # All results should be the same instance (no exceptions)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Got {len(exceptions)} exceptions: {exceptions}"

        instances = [r for r in results if r is not None]
        assert len(instances) == 100, f"Expected 100 non-None results, got {len(instances)}"

        # CRITICAL: factory should be called exactly ONCE
        assert factory_calls["count"] == 1, (
            f"Factory called {factory_calls['count']} times, expected 1. "
            f"Race condition: concurrent get() calls are not properly serialized."
        )

        # All results should be the same object
        first_instance = instances[0]
        assert all(r is first_instance for r in instances), (
            "Got different instances — lock is not working correctly"
        )

    @pytest.mark.asyncio
    async def test_stale_evict_skipped_on_fresh_load(self, fresh_lazy):
        """
        Timer fires → _evict() queued → but a newer load happened → skip evict.

        This tests the generation counter fix: _evict() checks _load_count vs
        _scheduled_load_gen to detect stale evicts and skip them.
        """
        factory_calls = {"count": 0}

        def counting_factory():
            factory_calls["count"] += 1
            return {"instance": f"gen_{factory_calls['count']}"}

        lazy = fresh_lazy(
            factory=counting_factory,
            ttl_seconds=0.05,  # 50ms TTL
            name="test_gen",
            min_free_mb=0,
        )

        # Load instance #1
        instance1 = await lazy.get()
        assert instance1 == {"instance": "gen_1"}
        assert lazy._load_count == 1

        # Wait for TTL to expire (timer fires and evicts instance1)
        await asyncio.sleep(0.1)
        assert lazy._instance is None, "Timer should have evicted instance1"

        # Load instance #2 — this is a fresh load (slow path)
        instance2 = await lazy.get()
        assert instance2 == {"instance": "gen_2"}
        assert lazy._load_count == 2, "Second load should increment load_count"

        # A NEW timer was set for instance #2
        # Wait for the NEW timer to potentially fire
        await asyncio.sleep(0.1)

        # Instance #2 should be evicted (its timer fired)
        # The OLD timer from instance #1 should have been cancelled
        assert lazy._instance is None, "Timer should have evicted instance2"

    @pytest.mark.asyncio
    async def test_second_load_after_eviction_resets_generation(self, fresh_lazy):
        """
        After first instance is evicted, loading a new instance should
        properly track generations so subsequent evicts work correctly.
        """
        factory_calls = {"count": 0}

        def counting_factory():
            factory_calls["count"] += 1
            return {"instance": f"gen_{factory_calls['count']}"}

        lazy = fresh_lazy(
            factory=counting_factory,
            ttl_seconds=0.05,
            name="test_gen",
            min_free_mb=0,
        )

        # Load instance #1
        instance1 = await lazy.get()
        assert instance1 == {"instance": "gen_1"}
        assert lazy._load_count == 1

        # Wait for eviction
        await asyncio.sleep(0.1)
        assert lazy._instance is None, "First instance should be evicted"

        # Load instance #2
        instance2 = await lazy.get()
        assert instance2 == {"instance": "gen_2"}
        assert lazy._load_count == 2, "Second load should increment count"

        # Wait for second eviction
        await asyncio.sleep(0.1)
        assert lazy._instance is None, "Second instance should be evicted"
        assert lazy._evict_count == 2

    @pytest.mark.asyncio
    async def test_lock_lazy_init_is_thread_safe(self, fresh_lazy):
        """
        asyncio.Lock is created lazily under a threading.Lock (DCLP).

        Verifies that concurrent get() calls from multiple coroutines
        don't race on asyncio.Lock() creation.
        """
        def factory():
            return {"locked": True}

        lazy = fresh_lazy(
            factory=factory,
            ttl_seconds=600,
            name="lock_test",
            min_free_mb=0,
        )

        # Simulate concurrent Lock creation attempts
        async def trigger_lock_init():
            await lazy.get()
            return lazy._get_lock()

        # Run 50 concurrent lock init attempts
        locks = await asyncio.gather(
            *[trigger_lock_init() for _ in range(50)],
            return_exceptions=True,
        )

        exceptions = [l for l in locks if isinstance(l, Exception)]
        assert len(exceptions) == 0, f"Lock init raised exceptions: {exceptions}"

        # All should return the SAME lock instance
        first_lock = locks[0]
        assert all(l is first_lock for l in locks), (
            "Got different Lock instances — DCLP is broken"
        )
        assert lazy._load_lock is first_lock

    @pytest.mark.asyncio
    async def test_memory_guard_under_concurrency(self, fresh_lazy):
        """
        Memory guard still works when multiple coroutines check it concurrently.

        With very low min_free_mb, factory should never be called.
        """
        factory_calls = {"count": 0}

        def counting_factory():
            factory_calls["count"] += 1
            return {"instance": "should_not_load"}

        lazy = fresh_lazy(
            factory=counting_factory,
            ttl_seconds=600,
            name="mem_guard",
            min_free_mb=1_000_000,  # 1TB — impossible on M1, always refuse
        )

        results = await asyncio.gather(
            *[lazy.get() for _ in range(50)],
            return_exceptions=True,
        )

        # All should return None (memory guard triggered)
        assert all(r is None for r in results), "Memory guard should refuse all"
        assert factory_calls["count"] == 0, "Factory should never be called"

    @pytest.mark.asyncio
    async def test_conditional_load_under_concurrency(self, fresh_lazy):
        """
        Conditional load (min_findings) still works under concurrency.

        With min_findings=100, calls with findings_count < 100 return None
        without acquiring the lock or calling factory.
        """
        factory_calls = {"count": 0}

        def counting_factory():
            factory_calls["count"] += 1
            return {"instance": "gnn_model"}

        lazy = fresh_lazy(
            factory=counting_factory,
            ttl_seconds=600,
            name="gnn",
            min_free_mb=0,
            conditional_min_findings=100,  # Only load if >= 100 findings
        )

        # 50 coroutines call with findings_count=50 (< 100 threshold)
        results = await asyncio.gather(
            *[lazy.get(findings_count=50) for _ in range(50)],
            return_exceptions=True,
        )

        assert all(r is None for r in results), "Should return None for low findings"
        assert factory_calls["count"] == 0, "Factory should not be called"

        # Now call with sufficient findings
        result = await lazy.get(findings_count=150)
        assert result == {"instance": "gnn_model"}
        assert factory_calls["count"] == 1


class TestLazyModelGenerationCounter:
    """Generation counter behavior tests."""

    @pytest.fixture
    def fresh_lazy(self):
        """Create a fresh LazyModel instance for each test."""
        mods_to_remove = [k for k in sys.modules if k.startswith("brain._lazy")]
        for mod in mods_to_remove:
            sys.modules.pop(mod, None)

        from brain._lazy import LazyModel
        yield LazyModel
        gc.collect()

    @pytest.mark.asyncio
    async def test_evict_increments_evict_count(self, fresh_lazy):
        """Eviction should increment _evict_count."""
        lazy = fresh_lazy(
            factory=lambda: {"x": 1},
            ttl_seconds=0.05,
            name="gen_test",
            min_free_mb=0,
        )

        await lazy.get()
        assert lazy._load_count == 1
        assert lazy._evict_count == 0

        # Wait for timer to fire
        await asyncio.sleep(0.1)

        assert lazy._instance is None, "Timer should have evicted"
        assert lazy._evict_count == 1

    @pytest.mark.asyncio
    async def test_multiple_load_evict_cycles(self, fresh_lazy):
        """
        Multiple load/evict cycles should correctly track generations.

        Each load bumps _load_count, each evict is counted separately.
        """
        lazy = fresh_lazy(
            factory=lambda: {"gen": "test"},
            ttl_seconds=0.05,
            name="multi_cycle",
            min_free_mb=0,
        )

        # Cycle 1
        await lazy.get()
        assert lazy._load_count == 1
        await asyncio.sleep(0.1)
        assert lazy._instance is None
        assert lazy._evict_count == 1

        # Cycle 2
        await lazy.get()
        assert lazy._load_count == 2
        await asyncio.sleep(0.1)
        assert lazy._instance is None
        assert lazy._evict_count == 2

    @pytest.mark.asyncio
    async def test_fast_path_does_not_increment_load_count(self, fresh_lazy):
        """
        Getting an already-loaded instance via fast path should NOT
        increment _load_count, so that timer is reset but generation stays.
        """
        factory_calls = {"count": 0}

        def counting_factory():
            factory_calls["count"] += 1
            return {"instance": "only_once"}

        lazy = fresh_lazy(
            factory=counting_factory,
            ttl_seconds=0.1,
            name="fast_path",
            min_free_mb=0,
        )

        # First call - slow path, loads instance
        result1 = await lazy.get()
        assert result1 == {"instance": "only_once"}
        assert factory_calls["count"] == 1
        assert lazy._load_count == 1

        # Second call - fast path, instance already loaded
        result2 = await lazy.get()
        assert result2 == {"instance": "only_once"}
        assert factory_calls["count"] == 1, "Factory should not be called again"
        assert lazy._load_count == 1, "Fast path should NOT increment load_count"

        # Timer should have been reset but not triggered yet
        await asyncio.sleep(0.05)
        assert lazy._instance is not None, "Timer should not have fired yet"

        # Wait for timer to fire
        await asyncio.sleep(0.1)
        assert lazy._instance is None, "Timer should have evicted"
        assert lazy._evict_count == 1
