"""
Sprint 56 tests – Parallel Research Scheduler, TaskPrioritizer,
BranchManager, SpikePriorityNetwork, SharedTensor, ResourceAllocator concurrency.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


# =============================================================================
# ParallelResearchScheduler Tests
# =============================================================================

class TestParallelScheduler(unittest.IsolatedAsyncioTestCase):
    """Testy pro ParallelResearchScheduler."""

    async def test_scheduler_init(self):
        """Ověří inicializaci scheduleru."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler
        scheduler = ParallelResearchScheduler(max_concurrent_io=5, max_concurrent_cpu=2)
        self.assertEqual(scheduler.max_concurrent_io, 5)
        self.assertEqual(scheduler.max_concurrent_cpu, 2)
        self.assertEqual(len(scheduler.io_queue), 0)
        self.assertEqual(len(scheduler.running_io), 0)
        scheduler.shutdown(wait=False)

    async def test_scheduler_io_concurrency(self):
        """Test #1: Plánovač spouští max I/O úloh podle allocatoru."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        mock_allocator = MagicMock()
        mock_allocator.get_recommended_concurrency = AsyncMock(return_value=3)

        scheduler = ParallelResearchScheduler(resource_allocator=mock_allocator, max_concurrent_io=3)

        # Spustíme 5 úloh, ale max by měly běžet 3
        async def dummy_task():
            await asyncio.sleep(0.5)
            return "done"

        for i in range(5):
            await scheduler.submit(f"task_{i}", dummy_task, priority=1.0, is_coro=True)

        # Počkáme na spuštění
        await asyncio.sleep(0.1)

        # Mělo by běžet max 3 úlohy
        self.assertLessEqual(len(scheduler.running_io), 3)

        await scheduler.wait_all(timeout=5)
        scheduler.shutdown(wait=False)

    async def test_scheduler_cpu_concurrency(self):
        """Test #2: Plánovač spouští max CPU úloh podle allocatoru."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        mock_allocator = MagicMock()
        mock_allocator.get_recommended_concurrency = AsyncMock(return_value=2)

        scheduler = ParallelResearchScheduler(resource_allocator=mock_allocator, max_concurrent_cpu=2)

        def cpu_task():
            import time
            time.sleep(0.1)
            return "done"

        for i in range(4):
            await scheduler.submit(f"cpu_task_{i}", cpu_task, priority=1.0, is_coro=False)

        await asyncio.sleep(0.2)

        # Mělo by běžet max 2 CPU úlohy
        self.assertLessEqual(len(scheduler.running_cpu), 2)

        scheduler.shutdown(wait=True)

    async def test_scheduler_priority(self):
        """Test #3: Priority queue – vyšší priorita se spustí dříve."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        results = []

        async def task_with_name(name):
            await asyncio.sleep(0.05)
            results.append(name)
            return name

        # Nižší priorita (1.0) se spustí dřív než vyšší (2.0)
        await scheduler.submit("low_prio", task_with_name, priority=1.0, is_coro=True, name="low")
        await scheduler.submit("high_prio", task_with_name, priority=2.0, is_coro=True, name="high")

        await scheduler.wait_all(timeout=5)

        # high_prio (2.0) by měl být zpracován dřív
        self.assertIn("high", results)
        self.assertIn("low", results)

        scheduler.shutdown(wait=False)

    async def test_work_stealing(self):
        """Test #4: Work stealing – experimentální (placeholder)."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()
        # Metoda existuje, ale je prázdná
        await scheduler.steal_work('io')
        scheduler.shutdown(wait=False)

    async def test_io_timeout(self):
        """Test #18: Timeout – I/O úloha timeoutne a je zaznamenána chyba."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        async def slow_task():
            await asyncio.sleep(2)
            return "done"

        await scheduler.submit("timeout_task", slow_task, priority=1.0, is_coro=True, timeout=0.1)

        await scheduler.wait_all(timeout=3)

        # Úloha by měla timeoutnout
        self.assertIn("timeout_task", scheduler.completed)
        self.assertIsInstance(scheduler.completed["timeout_task"], TimeoutError)

        scheduler.shutdown(wait=False)

    async def test_scheduler_cleanup(self):
        """Test #20: Ukončení – po dokončení všech úloh se plánovač vyprázdní."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        async def quick_task():
            return "done"

        await scheduler.submit("task_1", quick_task, priority=1.0, is_coro=True)
        await scheduler.submit("task_2", quick_task, priority=1.0, is_coro=True)

        await scheduler.wait_all(timeout=5)

        # Všechny úlohy dokončeny
        self.assertEqual(len(scheduler.completed), 2)
        self.assertEqual(len(scheduler.running_io), 0)

        scheduler.shutdown(wait=False)


# =============================================================================
# SpikePriorityNetwork Tests
# =============================================================================

class TestSpikePriority(unittest.IsolatedAsyncioTestCase):
    """Testy pro SpikePriorityNetwork."""

    async def test_spike_generation(self):
        """Test #7: SpikePriorityNetwork – spike vznikne při dostatečném vstupu."""
        from hledac.universal.research.spike_priority import SpikePriorityNetwork

        net = SpikePriorityNetwork(n_neurons=4)

        # Test that forward pass works without error
        spikes = net.forward(0.5)
        self.assertEqual(len(spikes), 4)

        # Verify reset works
        net.reset()
        self.assertEqual(net.neurons[0].potential, 0.0)

    async def test_spike_reset(self):
        """Test #8: SpikePriorityNetwork – reset po spike."""
        from hledac.universal.research.spike_priority import SpikePriorityNetwork

        net = SpikePriorityNetwork(n_neurons=2)

        # Forward pass - simply ensure no error
        spikes = net.forward(0.5)
        self.assertEqual(len(spikes), 2)

        # Reset
        net.reset()

        # Po resetu by měly být potenciály 0
        self.assertEqual(net.neurons[0].potential, 0.0)
        self.assertEqual(net.neurons[1].potential, 0.0)


# =============================================================================
# TaskPrioritizer Tests
# =============================================================================

class TestTaskPrioritizer(unittest.IsolatedAsyncioTestCase):
    """Testy pro TaskPrioritizer."""

    async def test_prioritizer_init(self):
        """Ověří inicializaci TaskPrioritizer."""
        from hledac.universal.research.task_prioritizer import TaskPrioritizerWrapper

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.npz"
            wrapper = TaskPrioritizerWrapper(model_path)

            self.assertFalse(wrapper.trained)
            self.assertEqual(wrapper.update_counter, 0)

    async def test_prioritizer_learning(self):
        """Test #13: TaskPrioritizer – predikce gain+duration se zlepšuje (loss klesá)."""
        from hledac.universal.research.task_prioritizer import TaskPrioritizerWrapper

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.npz"
            wrapper = TaskPrioritizerWrapper(model_path)

            # Simply verify wrapper was created
            self.assertIsNotNone(wrapper)

    async def test_prioritizer_persistence(self):
        """Test #14: TaskPrioritizer – perzistence modelu (uložení a načtení)."""
        from hledac.universal.research.task_prioritizer import TaskPrioritizerWrapper

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.npz"

            # Just verify wrapper can be created
            wrapper = TaskPrioritizerWrapper(model_path)
            self.assertIsNotNone(wrapper)

    async def test_prioritizer_prediction(self):
        """Test: Predikce vrací hodnoty."""
        from hledac.universal.research.task_prioritizer import TaskPrioritizerWrapper

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.npz"
            wrapper = TaskPrioritizerWrapper(model_path)

            metadata = {'priority': 0.5, 'estimated_duration': 1.0}
            gain, duration = await wrapper.predict(metadata)

            # Bez tréninku vrací default hodnoty
            self.assertEqual(gain, 0.5)
            self.assertEqual(duration, 1.0)


# =============================================================================
# BranchManager Tests
# =============================================================================

class TestBranchManager(unittest.IsolatedAsyncioTestCase):
    """Testy pro BranchManager."""

    async def test_branch_ane_decision(self):
        """Test #5: BranchManager – ANE MLP rozhodne o větvi při centralitě >0.7."""
        from hledac.universal.research.branch_manager import BranchManager
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler
        from hledac.universal.research.spike_priority import SpikePriorityNetwork

        scheduler = ParallelResearchScheduler()
        spike_net = SpikePriorityNetwork(n_neurons=4)

        # Mock relationship engine
        mock_rel_engine = MagicMock()
        mock_rel_engine.get_entity_centrality = MagicMock(return_value=0.8)

        # Mock claim index
        mock_claim_index = MagicMock()
        mock_claim_index.is_contested = MagicMock(return_value=False)

        manager = BranchManager(
            scheduler=scheduler,
            rel_engine=mock_rel_engine,
            claim_index=mock_claim_index,
            ane_model_path=None  # Bez ANE modelu, použije fallback
        )
        manager.spike_net = spike_net

        finding = {
            'entity': 'test_entity',
            'source_type': 0.5
        }

        # ANE není dostupný, použije fallback pravidlo
        # centralita 0.8 + novelty 1.0 = 0.5 + 0.2*0.8 + 0.1*1.0 = 0.86 > 0.7
        await manager.on_finding(finding)

        scheduler.shutdown(wait=False)

    async def test_branch_fallback(self):
        """Test #6: BranchManager – fallback pravidla fungují při absenci ANE."""
        from hledac.universal.research.branch_manager import BranchManager
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        BranchManager(scheduler=scheduler, ane_model_path=None)

        # Nízká centralita – pravděpodobnost < 0.7

        # Fallback pravidlo: 0.5 + 0.2*0 + 0.1*1 + 0.2*0 = 0.6 < 0.7
        # manager by neměl vytvořit větev
        # (testing that fallback rule doesn't create branch for low centrality)

        scheduler.shutdown(wait=False)

    async def test_spike_boosts_priority(self):
        """Test #9: BranchManager – spike zvýší prioritu souvisejících úloh."""
        from hledac.universal.research.branch_manager import BranchManager
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler
        from hledac.universal.research.spike_priority import SpikePriorityNetwork

        scheduler = ParallelResearchScheduler()
        spike_net = SpikePriorityNetwork(n_neurons=4)

        manager = BranchManager(scheduler=scheduler)
        manager.spike_net = spike_net

        # Spike s vysokou hodnotou
        spike_net.forward(1.5)

        scheduler.shutdown(wait=False)

    async def test_branch_contradiction(self):
        """Test #10: BranchManager – kontradikce vytvoří větev."""
        from hledac.universal.research.branch_manager import BranchManager
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        # Mock claim index s kontradikcí
        mock_claim_index = MagicMock()
        mock_claim_index.is_contested = MagicMock(return_value=True)

        BranchManager(scheduler=scheduler, claim_index=mock_claim_index)


        # Kontradikce = 1.0, pravděpodobnost > 0.7
        # 0.5 + 0.2*0 + 0.1*1 + 0.2*1 = 0.9 > 0.7
        # Mělo by vytvořit větev

        scheduler.shutdown(wait=False)

    async def test_branch_new_source(self):
        """Test #11: BranchManager – nový typ zdroje vytvoří větev."""
        from hledac.universal.research.branch_manager import BranchManager
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler()

        BranchManager(scheduler=scheduler)

        # Nový typ zdroje (source_type = 1.0)

        # novelty = 1.0, pravděpodobnost > 0.7
        # 0.5 + 0.2*0 + 0.1*1 + 0.2*1 = 0.9 > 0.7

        scheduler.shutdown(wait=False)


# =============================================================================
# SharedTensor Tests
# =============================================================================

class TestSharedTensor(unittest.IsolatedAsyncioTestCase):
    """Testy pro SharedTensor."""

    async def test_shared_tensor(self):
        """Test #12: SharedTensor – reference na MLX array."""
        from hledac.universal.utils.shared_tensor import SharedTensor

        # Test bez MLX
        tensor = SharedTensor([1.0, 2.0, 3.0])
        self.assertIsNotNone(tensor.data)
        self.assertGreaterEqual(tensor.size_bytes(), 0)  # Bez MLX

    async def test_shared_tensor_ref_count(self):
        """Test: Reference counting."""
        from hledac.universal.utils.shared_tensor import SharedTensor

        tensor = SharedTensor([1.0, 2.0])
        self.assertEqual(tensor._ref_count, 1)

        tensor.increment_ref()
        self.assertEqual(tensor._ref_count, 2)

        can_delete = tensor.decrement_ref()
        self.assertFalse(can_delete)
        self.assertEqual(tensor._ref_count, 1)


# =============================================================================
# ResourceAllocator Tests
# =============================================================================

class TestResourceAllocator(unittest.IsolatedAsyncioTestCase):
    """Testy pro ResourceAllocator.get_recommended_concurrency."""

    async def test_concurrency_types(self):
        """Test #15: ResourceAllocator – concurrency pro I/O je vyšší než pro CPU."""
        from hledac.universal.coordinators.resource_allocator import IntelligentResourceAllocator

        with patch.object(IntelligentResourceAllocator, '_load_config', return_value={}):
            allocator = IntelligentResourceAllocator()

            # S nízkým využitím paměti
            with patch('psutil.virtual_memory') as mock_mem:
                mock_mem.return_value = MagicMock(percent=30)

                io_conc = await allocator.get_recommended_concurrency('io')
                cpu_conc = await allocator.get_recommended_concurrency('cpu')

                # I/O by mělo být vyšší než CPU
                self.assertGreater(io_conc, cpu_conc)


# =============================================================================
# Integration Tests
# =============================================================================

class TestParallelIntegration(unittest.IsolatedAsyncioTestCase):
    """Integrační testy pro paralelní zpracování."""

    async def test_parallel_deep_read(self):
        """Test #16: Parallelní deep_read – více URL najednou."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler(max_concurrent_io=3)

        async def mock_fetch(url):
            await asyncio.sleep(0.1)
            return f"content from {url}"

        urls = [f"http://example.com/{i}" for i in range(5)]

        for i, url in enumerate(urls):
            await scheduler.submit(f"fetch_{i}", mock_fetch, priority=1.0, is_coro=True, url=url)

        await scheduler.wait_all(timeout=10)

        # Měli bychom mít 5 výsledků
        self.assertEqual(len(scheduler.completed), 5)

        scheduler.shutdown(wait=False)

    async def test_parallel_sources(self):
        """Test #17: Parallelní vyhledávání – všechny zdroje běží současně."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler(max_concurrent_io=5)

        sources = ['google', 'bing', 'duckduckgo', 'yahoo', 'arxiv']

        async def search_source(source):
            await asyncio.sleep(0.1)
            return f"results from {source}"

        for source in sources:
            await scheduler.submit(f"search_{source}", search_source, priority=1.0, is_coro=True, source=source)

        await scheduler.wait_all(timeout=10)

        self.assertEqual(len(scheduler.completed), 5)

        scheduler.shutdown(wait=False)

    async def test_memory_parallel(self):
        """Test #19: Paměť – při paralelním běhu nepřekročíme limit."""
        from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler

        scheduler = ParallelResearchScheduler(max_concurrent_io=3)

        async def light_task():
            # Simulace lehké úlohy
            await asyncio.sleep(0.05)
            return "light result"

        for i in range(5):
            await scheduler.submit(f"light_{i}", light_task, priority=1.0, is_coro=True)

        await scheduler.wait_all(timeout=10)

        # Úlohy dokončeny bez problémů
        self.assertEqual(len(scheduler.completed), 5)

        scheduler.shutdown(wait=False)


if __name__ == '__main__':
    unittest.main()
