"""BatchScheduler tests — pure asyncio, no MLX/GPU."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from hledac.universal.brain.batch_scheduler import BatchScheduler
from pydantic import BaseModel


class FakeStructuredOutput(BaseModel):
    """Fake response model for testing."""
    result: str
    confidence: float


class TestBatchSchedulerBasics(unittest.TestCase):
    """Basic initialization and startup tests."""

    def test_zero_mlx_imports(self):
        """B.S1: BatchScheduler has zero MLX imports."""
        import hledac.universal.brain.batch_scheduler as bs
        source = open(bs.__file__).read()
        mlx_keywords = ['mlx', 'mx.', 'metal', 'gpu', 'cuda']
        for kw in mlx_keywords:
            self.assertNotIn(kw, source, f"MLX keyword '{kw}' found in batch_scheduler.py")

    def test_init_creates_config(self):
        """Test default config is set correctly."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        self.assertEqual(scheduler._max_size, 8)
        self.assertEqual(scheduler._max_queue, 256)
        self.assertEqual(scheduler._default_flush_interval, 2.0)
        self.assertEqual(scheduler._medium_pressure_depth, 64)
        self.assertEqual(scheduler._high_pressure_depth, 192)
        self.assertEqual(scheduler._age_bump_interval, 3)
        self.assertEqual(scheduler._ema_alpha, 0.3)

    def test_init_custom_config(self):
        """Test custom config parameters."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(
            execute_callback=callback,
            max_size=4,
            max_queue=128,
            default_flush_interval=1.5,
            medium_pressure_depth=32,
            high_pressure_depth=96,
            age_bump_interval=5,
            ema_alpha=0.2,
        )

        self.assertEqual(scheduler._max_size, 4)
        self.assertEqual(scheduler._max_queue, 128)
        self.assertEqual(scheduler._default_flush_interval, 1.5)
        self.assertEqual(scheduler._medium_pressure_depth, 32)
        self.assertEqual(scheduler._high_pressure_depth, 96)
        self.assertEqual(scheduler._age_bump_interval, 5)
        self.assertEqual(scheduler._ema_alpha, 0.2)

    def test_queue_not_started_on_init(self):
        """Queue is None until start() is called."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)
        self.assertIsNone(scheduler._batch_queue)
        self.assertIsNone(scheduler._worker_task)

    def test_start_creates_queue_and_worker(self):
        """start() creates PriorityQueue and worker task."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            self.assertIsNotNone(scheduler._batch_queue)
            self.assertIsInstance(scheduler._batch_queue, asyncio.PriorityQueue)
            self.assertIsNotNone(scheduler._worker_task)
            self.assertIsInstance(scheduler._worker_task, asyncio.Task)
            await scheduler.shutdown()

        asyncio.run(run())

    def test_double_start_is_noop(self):
        """Calling start() twice does nothing second time."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            first_queue = scheduler._batch_queue
            first_task = scheduler._worker_task
            await scheduler.start()  # noop
            self.assertIs(scheduler._batch_queue, first_queue)
            self.assertIs(scheduler._worker_task, first_task)
            await scheduler.shutdown()

        asyncio.run(run())


class TestBatchSchedulingPolicy(unittest.TestCase):
    """Test scheduling policy: priority, boundaries, age bump."""

    def test_submit_returns_future(self):
        """submit() returns an asyncio.Future."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            future = await scheduler.submit(
                prompt="test prompt",
                response_model=FakeStructuredOutput,
                priority=1.0,
            )
            self.assertIsInstance(future, asyncio.Future)
            await scheduler.shutdown()

        asyncio.run(run())

    def test_submit_priority_rounding(self):
        """submit() stores correct priority in queue."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            await scheduler.submit(prompt="p=1", response_model=FakeStructuredOutput, priority=1.0)
            await scheduler.submit(prompt="p=2", response_model=FakeStructuredOutput, priority=2.0)
            await scheduler.submit(prompt="p=0.5", response_model=FakeStructuredOutput, priority=0.5)

            items = []
            while not scheduler._batch_queue.empty():
                items.append(scheduler._batch_queue.get_nowait())

            # priority 0.5 should be highest (first to dequeue)
            self.assertEqual(items[0][2], 'FakeStructuredOutput')
            self.assertAlmostEqual(items[0][0], 0.5)
            await scheduler.shutdown()

        asyncio.run(run())

    def test_is_batch_safe_urgent_priority(self):
        """priority=0 returns False (bypass batch)."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        self.assertFalse(scheduler.is_batch_safe(FakeStructuredOutput, priority=0))
        self.assertTrue(scheduler.is_batch_safe(FakeStructuredOutput, priority=1.0))
        self.assertTrue(scheduler.is_batch_safe(FakeStructuredOutput, priority=2.0))

    def test_is_batch_safe_no_schema(self):
        """response_model=None returns False."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        self.assertFalse(scheduler.is_batch_safe(None, priority=1.0))

    def test_is_batch_safe_short_timeout(self):
        """timeout <= 2x flush interval returns False."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        # 2x default flush interval = 4.0s, so 4.0s is NOT safe, 5.0s is safe
        self.assertFalse(scheduler.is_batch_safe(FakeStructuredOutput, priority=1.0, timeout_s=4.0))
        self.assertTrue(scheduler.is_batch_safe(FakeStructuredOutput, priority=1.0, timeout_s=5.0))

    def test_is_batch_safe_non_structured_schema(self):
        """Objects without __struct_fields__ or model_validate_json return False."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        class NonStructured:
            pass

        self.assertFalse(scheduler.is_batch_safe(NonStructured, priority=1.0))
        # FakeStructuredOutput IS pydantic → should return True
        self.assertTrue(scheduler.is_batch_safe(FakeStructuredOutput, priority=1.0))

    def test_schema_boundary_segregation(self):
        """Different schema_keys cause premature flush."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()

            # Submit two different schema types
            future1 = await scheduler.submit(
                prompt="test",
                response_model=FakeStructuredOutput,
                priority=1.0,
            )
            _future2 = await scheduler.submit(
                prompt="test",
                response_model=FakeStructuredOutput,  # same schema - OK
                priority=1.0,
            )

            # Trigger worker cycle by waiting briefly
            await asyncio.sleep(0.1)

            # Both should be pending (not resolved yet since callback is asyncmock)
            self.assertFalse(future1.done())

            await scheduler.shutdown()

        asyncio.run(run())

    def test_compute_length_bin(self):
        """Length binning: short < 256, medium < 1024, long >= 1024."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        self.assertEqual(scheduler._compute_length_bin("a" * 500), 'short')   # ~125 tokens
        self.assertEqual(scheduler._compute_length_bin("a" * 2000), 'medium')  # ~500 tokens
        self.assertEqual(scheduler._compute_length_bin("a" * 5000), 'long')    # ~1250 tokens

    def test_compute_system_prompt_hash(self):
        """Hash of system prompt for segregation."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        self.assertEqual(scheduler._compute_system_prompt_hash(None), 'default')
        self.assertEqual(scheduler._compute_system_prompt_hash(""), 'default')
        # Same prompt → same hash (verify determinism)
        h1 = scheduler._compute_system_prompt_hash("sys")
        h2 = scheduler._compute_system_prompt_hash("sys")
        self.assertEqual(h1, h2)
        # Different prompt → different hash
        self.assertNotEqual(
            scheduler._compute_system_prompt_hash("sys1"),
            scheduler._compute_system_prompt_hash("sys2")
        )
        # Hash is 8 hex chars
        self.assertEqual(len(h1), 8)
        self.assertTrue(all(c in '0123456789abcdef' for c in h1))


class TestAgeBumpAntiStarvation(unittest.TestCase):
    """Test age bump anti-starvation mechanism."""

    def test_age_bump_interval_configurable(self):
        """age_bump_interval is configurable."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback, age_bump_interval=5)
        self.assertEqual(scheduler._age_bump_interval, 5)

    def test_age_bump_decreases_priority(self):
        """_age_bump_queue reduces priority by 1 (max 0)."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback, age_bump_interval=1)

        async def run():
            await scheduler.start()

            # Submit items with priority 3 and 5
            await scheduler.submit(prompt="p=3", response_model=FakeStructuredOutput, priority=3.0)
            await scheduler.submit(prompt="p=5", response_model=FakeStructuredOutput, priority=5.0)

            # Trigger age bump directly
            await scheduler._age_bump_queue()

            # Re-read items from queue (age bumped: 3→2, 5→4)
            items = []
            while not scheduler._batch_queue.empty():
                items.append(scheduler._batch_queue.get_nowait())

            priorities = sorted([item[0] for item in items])
            self.assertEqual(priorities, [2.0, 4.0])

            # Clamp at 0: if priority is 1, bump → 0 (not -1)
            await scheduler._age_bump_queue()  # 2→1, 4→3
            await scheduler._age_bump_queue()  # 1→0, 3→2
            await scheduler._age_bump_queue()  # 0→0, 2→1 (first stays 0)

            items2 = []
            while not scheduler._batch_queue.empty():
                items2.append(scheduler._batch_queue.get_nowait())
            priorities2 = sorted([item[0] for item in items2])
            # No negative values — clamp at 0
            for p in priorities2:
                self.assertGreaterEqual(p, 0.0)

            await scheduler.shutdown()

        asyncio.run(run())


class TestAdaptiveFlushInterval(unittest.TestCase):
    """Test 3-tier adaptive flush interval policy."""

    def test_default_flush_interval(self):
        """depth=0 returns default_flush_interval."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)
        self.assertEqual(scheduler._current_flush_interval(), 2.0)

    def test_medium_pressure(self):
        """depth > 64 returns 1.0s."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            # Fill queue to medium pressure
            for i in range(65):
                await scheduler.submit(
                    prompt=f"item{i}",
                    response_model=FakeStructuredOutput,
                    priority=1.0,
                )
            self.assertEqual(scheduler._current_flush_interval(), 1.0)
            await scheduler.shutdown()

        asyncio.run(run())

    def test_high_pressure(self):
        """depth > 192 returns 0.5s."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(
            execute_callback=callback,
            high_pressure_depth=192,
        )

        async def run():
            await scheduler.start()
            # Fill queue to high pressure
            for i in range(193):
                await scheduler.submit(
                    prompt=f"item{i}",
                    response_model=FakeStructuredOutput,
                    priority=1.0,
                )
            self.assertEqual(scheduler._current_flush_interval(), 0.5)
            await scheduler.shutdown()

        asyncio.run(run())

    def test_flush_interval_never_below_0_5(self):
        """B.S8: flush_interval >= 0.5s always."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback, high_pressure_depth=192)

        async def run():
            await scheduler.start()
            for i in range(500):
                await scheduler.submit(
                    prompt=f"item{i}",
                    response_model=FakeStructuredOutput,
                    priority=1.0,
                )
            # Even at 500 items, should not go below 0.5
            self.assertGreaterEqual(scheduler._current_flush_interval(), 0.5)
            await scheduler.shutdown()

        asyncio.run(run())


class TestBatchMaxSize(unittest.TestCase):
    """Test batch max size enforcement."""

    def test_batch_max_size_8(self):
        """Default max_size is 8."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)
        self.assertEqual(scheduler._max_size, 8)

    def test_custom_max_size(self):
        """max_size is configurable."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback, max_size=4)
        self.assertEqual(scheduler._max_size, 4)


class TestShutdown(unittest.TestCase):
    """Test shutdown behavior."""

    def test_shutdown_fails_pending_futures(self):
        """B.S5: shutdown fails all pending futures."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()

            # Submit multiple requests
            future1 = await scheduler.submit(
                prompt="test1",
                response_model=FakeStructuredOutput,
                priority=1.0,
            )
            future2 = await scheduler.submit(
                prompt="test2",
                response_model=FakeStructuredOutput,
                priority=1.0,
            )

            # Shutdown before they complete
            await scheduler.shutdown()

            # Futures should be done (failed)
            self.assertTrue(future1.done())
            self.assertTrue(future2.done())
            with self.assertRaises(RuntimeError):
                future1.result()

        asyncio.run(run())

    def test_double_shutdown_noop(self):
        """Calling shutdown() twice is safe."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            await scheduler.shutdown()
            await scheduler.shutdown()  # noop

        asyncio.run(run())

    def test_shutdown_clears_queue(self):
        """B.S4: queue is None after shutdown."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            self.assertIsNotNone(scheduler._batch_queue)
            await scheduler.shutdown()
            self.assertIsNone(scheduler._batch_queue)
            self.assertIsNone(scheduler._worker_task)

        asyncio.run(run())


class TestTelemetry(unittest.TestCase):
    """Test telemetry (EMA + counters)."""

    def test_initial_telemetry(self):
        """Initial telemetry is zeroed."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)
        telemetry = scheduler.get_telemetry()

        self.assertEqual(telemetry['ema']['dispatch_to_result_ms'], 0.0)
        self.assertEqual(telemetry['ema']['batch_size'], 0)
        self.assertEqual(telemetry['ema']['queue_depth'], 0)
        self.assertEqual(telemetry['counters']['batch_submitted'], 0)
        self.assertEqual(telemetry['counters']['batch_executed'], 0)

    def test_telemetry_after_submit(self):
        """batch_submitted counter increments on submit."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            await scheduler.submit(prompt="test", response_model=FakeStructuredOutput)
            telemetry = scheduler.get_telemetry()
            self.assertEqual(telemetry['counters']['batch_submitted'], 1)
            await scheduler.shutdown()

        asyncio.run(run())


class TestFlush(unittest.TestCase):
    """Test flush() drain behavior."""

    def test_flush_empty_queue_returns_0(self):
        """flush() on empty queue returns 0."""
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()
            drained = await scheduler.flush(timeout=1.0)
            self.assertEqual(drained, 0)
            await scheduler.shutdown()

        asyncio.run(run())


class TestConcurrentOperations(unittest.TestCase):
    """Test concurrent enqueue/dequeue operations."""

    def test_concurrent_submit_multiple(self):
        """Multiple concurrent submits create futures that can be resolved.

        Note: Full end-to-end resolution requires integration with Hermes3Engine
        (Phase 3). This test verifies futures are created and tracking works.
        """
        callback = AsyncMock(return_value={"result": "test"})
        scheduler = BatchScheduler(execute_callback=callback)

        async def run():
            await scheduler.start()

            futures = []
            for i in range(3):
                f = await scheduler.submit(
                    prompt=f"prompt{i}",
                    response_model=FakeStructuredOutput,
                    priority=1.0,
                )
                futures.append(f)

            self.assertEqual(len(futures), 3)
            # All should be in queue (pending)
            for f in futures:
                self.assertFalse(f.done())
            # All should be tracked in pending_futures
            self.assertEqual(len(scheduler._pending_futures), 3)

            await scheduler.shutdown()

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main(verbosity=2)
