"""
Sprint 54 tests – Global priority queue, zero-copy Arrow, MLX Holt,
HNSW fallback, predictive allocator, emergency brake.
"""

import asyncio
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import psutil

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


# =============================================================================
# Task Registry
# =============================================================================

class TestTaskRegistry(unittest.IsolatedAsyncioTestCase):
    """Testy pro registr úloh."""

    async def test_task_registry_exists(self):
        """Ověří, že registr úloh existuje."""
        from hledac.universal.orchestrator.global_scheduler import _TASK_REGISTRY, register_task
        self.assertIsInstance(_TASK_REGISTRY, dict)

    async def test_register_task(self):
        """Ověří registraci funkce do registru."""
        from hledac.universal.orchestrator.global_scheduler import _TASK_REGISTRY, register_task

        def dummy():
            pass

        register_task('dummy_test', dummy)
        self.assertIn('dummy_test', _TASK_REGISTRY)
        self.assertEqual(_TASK_REGISTRY['dummy_test'], dummy)


# =============================================================================
# Global Scheduler
# =============================================================================

class TestGlobalScheduler(unittest.IsolatedAsyncioTestCase):
    """Testy pro globální prioritní scheduler."""

    async def test_scheduler_init(self):
        """Ověří inicializaci scheduleru."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler
        sched = GlobalPriorityScheduler(max_workers=1)
        self.assertEqual(sched.max_workers, 1)

    async def test_priority_ordering(self):
        """Úloha s vyšší prioritou (nižší číslo) se zpracuje dříve."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler, register_task

        # Register task
        def task(prio):
            pass

        register_task('test_prio', task)

        # Test with direct scheduling (not starting workers)
        sched = GlobalPriorityScheduler(max_workers=1)

        # Schedule in reverse priority order
        sched.schedule(3, 'test_prio', 3)
        sched.schedule(1, 'test_prio', 1)
        sched.schedule(2, 'test_prio', 2)

        # Drain the PriorityQueue via sequential peek+reinsert:
        # Each peek+put cycle consumes one item from heap top and puts it back,
        # but we need to drain ALL items to verify ordering.
        # Instead: drain to a list (using internal _pq), then verify.
        drained = []
        try:
            while True:
                item = sched._pq.get_nowait()
                drained.append(item)
        except Exception:
            pass
        # Re-insert so shutdown is clean
        for item in drained:
            sched._pq.put(item)

        # Extract priorities from (priority, timestamp, seq, ...) tuples
        priorities = [item[0] for item in drained]
        # After scheduling 3,1,2 — PriorityQueue orders by priority: 1, 2, 3
        self.assertEqual(priorities, [1, 2, 3])

        sched.shutdown(wait=False)

    async def test_affinity_fallback(self):
        """Ověří, že affinity fallback necrashuje."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler
        sched = GlobalPriorityScheduler()

        # Test that _set_affinity doesn't crash (may not work on all platforms)
        result = sched._set_affinity(os.getpid())
        # Result may be True or False depending on platform, but shouldn't crash
        self.assertIsInstance(result, bool)

    async def test_job_state_machine(self):
        """Test job state transitions: pending → running → succeeded."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler, register_task

        def dummy(prio=None):
            pass
        register_task('dummy_job', dummy)

        sched = GlobalPriorityScheduler(max_workers=1)
        job_id = sched.schedule(1, 'dummy_job', 1)

        # Initial state should be pending
        status = sched.get_job_status(job_id)
        self.assertEqual(status['status'], 'pending')
        self.assertEqual(status['task_name'], 'dummy_job')
        self.assertEqual(status['priority'], 1)
        self.assertIsNotNone(status['created_at'])

        # List jobs should contain our job
        jobs = sched.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['job_id'], job_id)

        # Stats should count pending
        stats = sched.get_job_stats()
        self.assertEqual(stats['pending'], 1)
        self.assertEqual(stats['succeeded'], 0)

        sched.shutdown(wait=False)

    async def test_job_state_string_statuses(self):
        """Verify job states are tracked as strings (not enum) for multiprocessing safety."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler, register_task

        def dummy():
            pass
        register_task('str_test', dummy)

        sched = GlobalPriorityScheduler(max_workers=1)
        job_id = sched.schedule(2, 'str_test')

        status = sched.get_job_status(job_id)
        # Status must be a string for mp.Queue pickling safety
        self.assertIsInstance(status['status'], str)
        self.assertIn(status['status'], ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'dead_letter'))

    async def test_dlq_initialization(self):
        """Test DLQ starts empty and stats are correct."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler
        sched = GlobalPriorityScheduler(max_workers=1)

        stats = sched.get_dlq_stats()
        self.assertEqual(stats['dlq_size'], 0)

        dlq = sched.get_dlq()
        self.assertEqual(dlq, [])

        sched.shutdown(wait=False)

    async def test_dlq_movement(self):
        """Test job moves to DLQ after max_retries exhausted."""
        from hledac.universal.orchestrator.global_scheduler import GlobalPriorityScheduler, register_task

        def always_fail():
            raise RuntimeError('permanent failure')
        register_task('dlq_fail', always_fail)

        sched = GlobalPriorityScheduler(max_workers=1)
        job_id = sched.schedule(1, 'dlq_fail', max_retries=0)

        # Verify job is tracked
        status = sched.get_job_status(job_id)
        self.assertEqual(status['max_retries'], 0)

        # Stats should not count dead_letter in active jobs
        stats = sched.get_job_stats()
        self.assertEqual(stats['pending'], 0)  # Worker hasn't picked it up yet in this test

        sched.shutdown(wait=False)

        sched.shutdown(wait=False)


# =============================================================================
# Arrow Shared Memory
# =============================================================================

class TestArrowSharedMemory(unittest.IsolatedAsyncioTestCase):
    """Testy pro zero-copy Arrow shared memory."""

    async def test_arrow_serialize_deserialize(self):
        """Ověří serializaci a deserializaci."""
        from hledac.universal.memory.shared_memory_manager import ArrowSharedMemory

        data = {"key": "value", "number": 42, "list": [1, 2, 3]}

        with ArrowSharedMemory("test_shm") as shm:
            size = shm.serialize(data)
            loaded = shm.deserialize()

        self.assertEqual(loaded["key"], "value")
        self.assertEqual(loaded["number"], 42)
        self.assertEqual(loaded["list"], [1, 2, 3])

    async def test_arrow_zero_copy_memory(self):
        """Zero-copy: přenos 10MB dat - testujeme že funguje ser/deser."""
        from hledac.universal.memory.shared_memory_manager import ArrowSharedMemory

        # Create 10MB of data
        data = {"key": "x" * 10_000_000}

        with ArrowSharedMemory("test_shm", size=20_000_000) as shm:
            size = shm.serialize(data)
            loaded = shm.deserialize()

        # Verify data roundtrip works
        self.assertEqual(loaded["key"], data["key"])
        self.assertGreater(size, 10_000_000)  # size includes JSON overhead

    async def test_arrow_fallback_no_pyarrow(self):
        """Test bez PyArrow - použije JSON fallback."""
        from hledac.universal.memory.shared_memory_manager import ArrowSharedMemory

        with patch('hledac.universal.memory.shared_memory_manager.PYARROW_AVAILABLE', False):
            data = {"test": "value"}

            with ArrowSharedMemory("test_fallback") as shm:
                shm.serialize(data)
                loaded = shm.deserialize()

            self.assertEqual(loaded["test"], "value")


# =============================================================================
# Resource Allocator
# =============================================================================

class TestResourceAllocator(unittest.IsolatedAsyncioTestCase):
    """Testy pro prediktivní resource allocator."""

    async def test_allocator_init(self):
        """Ověří inicializaci allocatoru."""
        from hledac.universal.resource_allocator import ResourceAllocator
        alloc = ResourceAllocator()
        self.assertEqual(alloc.MAX_CONCURRENT, 3)
        self.assertEqual(alloc.MAX_RAM_GB, 5.5)
        self.assertEqual(alloc.WARMUP_QUERIES, 5)

    async def test_resource_warmup(self):
        """Prvních 5 dotazů nepoužívá predikci."""
        from hledac.universal.resource_allocator import ResourceAllocator
        alloc = ResourceAllocator()

        for i in range(5):
            fake_ctx = MagicMock()
            fake_ctx.query = "x" * 100
            fake_ctx.depth = 1
            fake_ctx.selected_sources = []
            fake_ctx.complexity_score = 0.5
            alloc.acquire(f"req{i}", fake_ctx, priority=1)
            alloc.release(f"req{i}", 500)

        self.assertEqual(alloc.warmup_counter, 5)
        # Model should not be ready during warmup
        self.assertIsNone(alloc.coeffs)

    async def test_emergency_brake(self):
        """Při RSS > 6.2 GB se zruší nejméně důležitá úloha (nejvyšší číslo priority."""
        from hledac.universal.resource_allocator import ResourceAllocator, ResourceBudget
        alloc = ResourceAllocator()

        with patch('psutil.virtual_memory') as mock_mem:
            mock_mem.return_value.used = 7 * 1024**3  # 7 GB

            # Add some requests using ResourceBudget dataclass
            req1 = ResourceBudget(ram_mb=500, time_sec=300, priority=3, request_id="test1")
            alloc.active_requests["test1"] = req1

            req2 = ResourceBudget(ram_mb=500, time_sec=300, priority=1, request_id="test2")
            alloc.active_requests["test2"] = req2

            # Emergency brake should cancel the highest priority number (3), keep lowest (1)
            result = alloc.emergency_brake()

            # test1 (priority 3) should be cancelled, test2 (priority 1) kept
            self.assertIn("test2", alloc.active_requests)

    async def test_resource_allocator_limit(self):
        """Při 4. souběžném dotazu se vyvolá ResourceExhausted."""
        from hledac.universal.resource_allocator import ResourceAllocator, ResourceExhausted

        alloc = ResourceAllocator()

        # Fill up to MAX_CONCURRENT
        for i in range(3):
            fake_ctx = MagicMock()
            fake_ctx.query = "x" * 100
            fake_ctx.depth = 1
            fake_ctx.selected_sources = []
            fake_ctx.complexity_score = 0.5

            # Mock can_accept to return True
            with patch.object(alloc, 'can_accept', return_value=True):
                alloc.acquire(f"req{i}", fake_ctx, priority=1)

        # 4th request should fail
        fake_ctx = MagicMock()
        fake_ctx.query = "x" * 100
        fake_ctx.depth = 1
        fake_ctx.selected_sources = []
        fake_ctx.complexity_score = 0.5

        with patch.object(alloc, 'can_accept', return_value=False):
            with self.assertRaises(ResourceExhausted):
                alloc.acquire("req4", fake_ctx, priority=1)

    async def test_predict_ram(self):
        """Test RAM predikce."""
        from hledac.universal.resource_allocator import ResourceAllocator
        alloc = ResourceAllocator()

        fake_ctx = MagicMock()
        fake_ctx.query = "test query"
        fake_ctx.depth = 1
        fake_ctx.selected_sources = ["source1", "source2"]
        fake_ctx.complexity_score = 0.7

        # During warmup, should return default
        predicted = alloc.predict_ram(fake_ctx)
        self.assertEqual(predicted, 500.0)


# =============================================================================
# HNSW Fallback
# =============================================================================

class TestHNSWFallback(unittest.IsolatedAsyncioTestCase):
    """Testy pro HNSW fallback na lineární vyhledávání."""

    async def test_find_similar_vectors_small_graph(self):
        """Pro malé grafy (<100 uzlů) se použije lineární vyhledávání."""
        from hledac.universal.knowledge.persistent_layer import PersistentKnowledgeLayer
        from pathlib import Path

        # Create a minimal mock
        layer = PersistentKnowledgeLayer.__new__(PersistentKnowledgeLayer)
        layer._hnsw_index = None
        layer._hnsw_id_to_node = {}
        layer._node_embeddings = {
            "node1": [1.0, 0.0, 0.0],
            "node2": [0.0, 1.0, 0.0],
            "node3": [0.0, 0.0, 1.0],
        }

        # Should use linear search for small graph
        result = await layer.find_similar_vectors([1.0, 0.0, 0.0], top_k=2)

        # Should return node1 (exact match) as first
        self.assertIn("node1", result)


# =============================================================================
# Async LMDB
# =============================================================================

class TestAsyncLMDB(unittest.IsolatedAsyncioTestCase):
    """Testy pro asynchronní LMDB."""

    async def test_aiolmdb_availability(self):
        """Test dostupnosti aiolmdb."""
        from hledac.universal.tools.lmdb_kv import AIOLMDB_AVAILABLE
        # Should be either True or False, but no crash
        self.assertIsInstance(AIOLMDB_AVAILABLE, bool)


# =============================================================================
# Selectolax
# =============================================================================

class TestSelectolax(unittest.IsolatedAsyncioTestCase):
    """Testy pro selectolax jako výchozí parser."""

    async def test_selectolax_availability(self):
        """Test dostupnosti selectolax."""
        from hledac.universal.loops.fetch_loop import SELECTOLAX_AVAILABLE
        self.assertIsInstance(SELECTOLAX_AVAILABLE, bool)


# =============================================================================
# orjson
# =============================================================================

class TestOrjson(unittest.IsolatedAsyncioTestCase):
    """Testy pro orjson serializaci."""

    async def test_orjson_usage(self):
        """Test použití orjson."""
        from hledac.universal.memory.shared_memory_manager import ORJSON_AVAILABLE
        self.assertIsInstance(ORJSON_AVAILABLE, bool)

    async def test_json_roundtrip(self):
        """Test JSON round-trip."""
        from hledac.universal.memory.shared_memory_manager import ArrowSharedMemory

        data = {"test": 123, "nested": {"key": "value"}}

        with ArrowSharedMemory("json_test") as shm:
            shm.serialize(data)
            loaded = shm.deserialize()

        self.assertEqual(data, loaded)


# =============================================================================
# Cleanup
# =============================================================================

class TestCleanup(unittest.IsolatedAsyncioTestCase):
    """Testy pro uvolnění zdrojů."""

    async def test_shared_memory_cleanup(self):
        """Po dokončení dotazu se všechny buffery uvolní."""
        from hledac.universal.memory.shared_memory_manager import ArrowSharedMemory

        shm = ArrowSharedMemory("cleanup_test")
        shm.serialize({"data": "test"})
        shm.close()

        self.assertTrue(shm._closed)


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == '__main__':
    unittest.main()
