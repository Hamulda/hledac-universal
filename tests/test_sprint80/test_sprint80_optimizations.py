"""
Tests for Sprint 80 - Progressive-deepening evidence engine.
"""
import asyncio
import time
from collections import OrderedDict

import pytest


class TestThreadPools:
    """Tests for thread_pools utility."""

    def test_get_core_counts(self):
        """Test core count detection."""
        from hledac.universal.utils.thread_pools import get_core_counts
        counts = get_core_counts()
        assert 'p_cores' in counts
        assert 'e_cores' in counts
        assert counts['p_cores'] > 0
        assert counts['e_cores'] > 0

    def test_get_io_pool(self):
        """Test I/O pool creation."""
        from hledac.universal.utils.thread_pools import get_io_pool
        pool = get_io_pool()
        assert pool is not None
        assert pool._max_workers > 0

    def test_get_cpu_pool(self):
        """Test CPU pool creation."""
        from hledac.universal.utils.thread_pools import get_cpu_pool
        pool = get_cpu_pool()
        assert pool is not None
        assert pool._max_workers > 0


class TestTokenBucketController:
    """Tests for TokenBucketController."""

    @pytest.mark.asyncio
    async def test_token_bucket_acquire(self):
        """Test token bucket acquire."""
        from hledac.universal.stealth.stealth_manager import TokenBucketController

        tb = TokenBucketController(rate=10, capacity=5)
        await tb.acquire()  # Should succeed immediately
        # Token consumed

    @pytest.mark.asyncio
    async def test_token_bucket_refill(self):
        """Test token bucket refill over time."""
        from hledac.universal.stealth.stealth_manager import TokenBucketController

        tb = TokenBucketController(rate=100, capacity=1)
        await tb.acquire()
        # Wait for refill
        await asyncio.sleep(0.05)
        await tb.acquire()  # Should get refill


class TestBoundedHostState:
    """Tests for BoundedHostState."""

    def test_bounded_host_state_maxlen(self):
        """Test LRU eviction."""
        from hledac.universal.stealth.stealth_manager import BoundedHostState

        bhs = BoundedHostState(maxlen=3)
        bhs['a'] = 1
        bhs['b'] = 2
        bhs['c'] = 3
        assert len(bhs) == 3

        bhs['d'] = 4  # Should evict 'a'
        assert 'a' not in bhs
        assert 'd' in bhs


class TestHostTelemetry:
    """Tests for HostTelemetry."""

    def test_host_telemetry_slots(self):
        """Test __slots__ are defined."""
        from hledac.universal.stealth.stealth_manager import HostTelemetry
        import asyncio

        sem = asyncio.Semaphore(2)
        ht = HostTelemetry(sem)
        assert hasattr(ht, 'semaphore')
        assert hasattr(ht, 'errors')
        assert hasattr(ht, 'latencies')
        assert ht.errors == 0


class TestMemoryPressurePoller:
    """Tests for MemoryPressurePoller."""

    @pytest.mark.asyncio
    async def test_pressure_poller_creation(self):
        """Test poller creation."""
        from hledac.universal.coordinators.memory_coordinator import MemoryPressurePoller

        poller = MemoryPressurePoller(interval=1.0)
        assert poller._interval == 1.0
        assert poller._level == 0.1  # Default

    @pytest.mark.asyncio
    async def test_pressure_poller_get_level(self):
        """Test get_level returns value."""
        from hledac.universal.coordinators.memory_coordinator import MemoryPressurePoller

        poller = MemoryPressurePoller()
        level = poller.get_level()
        assert 0.0 <= level <= 1.0


class TestEntityGraph:
    """Tests for _EntityGraph."""

    def test_entity_graph_add_edge(self):
        """Test adding edges."""
        from hledac.universal.autonomous_orchestrator import _EntityGraph

        graph = _EntityGraph(max_nodes=100)
        graph.add_edge('entity1', 'entity2', depth=1)
        assert graph.degree('entity1') == 1
        assert graph.in_degree('entity2') == 1

    def test_entity_graph_voi_score(self):
        """Test VoI score calculation."""
        from hledac.universal.autonomous_orchestrator import _EntityGraph

        graph = _EntityGraph()
        graph.add_edge('A', 'B', depth=1)
        graph.add_edge('A', 'C', depth=1)
        graph.add_edge('B', 'A', depth=1)

        score = graph.voi_score('A')
        assert 0 <= score <= 1.0

    def test_entity_graph_is_visited(self):
        """Test visited check."""
        from hledac.universal.autonomous_orchestrator import _EntityGraph

        graph = _EntityGraph()
        assert graph.is_visited('test') is False
        graph.add_edge('test', 'other', depth=1)
        assert graph.is_visited('test') is True


class TestNEREngineExtensions:
    """Tests for NER engine MLX extensions."""

    def test_ner_engine_has_mlx_class_attrs(self):
        """Test NEREngine has MLX class attributes."""
        from hledac.universal.brain.ner_engine import NEREngine

        assert hasattr(NEREngine, '_MLX_AVAILABLE')
        assert hasattr(NEREngine, '_MLX_EXTRACTOR')

    def test_ner_engine_mlx_not_available_by_default(self):
        """Test MLX is not available without dependencies."""
        from hledac.universal.brain.ner_engine import NEREngine

        # Should be False unless outlines+mlx installed
        assert NEREngine._MLX_AVAILABLE is False


class TestOSINTAdapters:
    """Tests for OSINT adapters - skipped due to import order issues."""

    def test_osint_adapters_disabled(self):
        """Placeholder - adapters tested separately."""
        pytest.skip("Adapters require direct import order fix")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
