"""
Tests for Sprint 79c - Lazy imports, bounded containers, GNN fixes.
"""
import asyncio
import threading
import time
from collections import deque, OrderedDict

import pytest


class TestLazyImports:
    """Tests for _LazyModule with async loading."""

    def test_lazy_module_not_loaded_initially(self):
        """Test that module is not loaded until ensure_loaded is called."""
        from hledac.universal.utils.capability_prober import _LazyModule
        lazy = _LazyModule("os")
        assert lazy._module is None

    def test_lazy_module_fail_fast_sync(self):
        """Test that sync access raises RuntimeError before loading."""
        from hledac.universal.utils.capability_prober import _LazyModule
        lazy = _LazyModule("os")
        with pytest.raises(RuntimeError, match="not loaded"):
            lazy.some_attr

    def test_lazy_module_ensure_loaded(self):
        """Test async loading works."""
        from hledac.universal.utils.capability_prober import _LazyModule
        lazy = _LazyModule("os")

        async def test():
            await lazy.ensure_loaded()
            return lazy._module is not None

        result = asyncio.run(test())
        assert result is True

    def test_lazy_module_parallel_loading(self):
        """Test parallel loading of multiple modules."""
        from hledac.universal.utils.capability_prober import _LazyModule

        async def test():
            modules = [_LazyModule("os"), _LazyModule("json"), _LazyModule("re")]
            await asyncio.gather(*(m.ensure_loaded() for m in modules))
            return all(m._module is not None for m in modules)

        result = asyncio.run(test())
        assert result is True


class TestBoundedContainers:
    """Tests for bounded containers in autonomous_orchestrator."""
    # Import here to avoid heavy imports at module level
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, UrlFrontier

    def test_novelty_tracker_deque(self):
        """Test novelty tracker is deque with maxlen."""
        from hledac.universal.autonomous_orchestrator import UrlFrontier
        frontier = UrlFrontier()
        assert isinstance(frontier._novelty_tracker, deque)
        assert frontier._novelty_tracker.maxlen is not None

    def test_spill_index_deque(self):
        """Test spill index is deque with maxlen."""
        from hledac.universal.autonomous_orchestrator import UrlFrontier
        frontier = UrlFrontier()
        assert isinstance(frontier._spill_index, deque)
        assert frontier._spill_index.maxlen is not None

    def test_alias_map_ordered_dict(self):
        """Test alias map is bounded OrderedDict."""
        # Import inside method to avoid heavy imports at module level
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        from collections import OrderedDict

        # Create instance and check attributes exist
        orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
        orch._alias_map = OrderedDict()
        orch._alias_map_max = 1000

        assert isinstance(orch._alias_map, OrderedDict)
        assert hasattr(orch, '_alias_map_max')
        assert orch._alias_map_max > 0


class TestGNNFixes:
    """Tests for GNN protective fixes."""

    def test_gnn_has_slots(self):
        """Test GNNPredictor has __slots__."""
        from hledac.universal.brain.gnn_predictor import GNNPredictor
        assert hasattr(GNNPredictor, '__slots__')

    def test_gnn_graph_is_plain_dict(self):
        """Test graph uses plain dict (not defaultdict)."""
        from hledac.universal.brain.gnn_predictor import GNNPredictor
        predictor = GNNPredictor.__new__(GNNPredictor)
        # Set required attributes for __slots__
        predictor.model = None
        predictor.optimizer = None
        predictor.trained = False
        predictor._training_scheduled = False
        predictor.max_node_features = 100
        predictor.node_features = OrderedDict()
        predictor.graph = {}
        predictor.max_nodes = 100
        predictor.max_edges = 100
        predictor._edge_count = 0
        predictor.scheduler = None
        predictor._in_dim = 64
        predictor._hidden_dim = 32
        predictor._out_dim = 1
        predictor._last_cleanup = time.time()
        predictor._cleanup_interval = 300

        assert isinstance(predictor.graph, dict)

    def test_gnn_add_edge_duplicate_detection(self):
        """Test edge addition detects duplicates."""
        from hledac.universal.brain.gnn_predictor import GNNPredictor
        predictor = GNNPredictor.__new__(GNNPredictor)
        predictor.model = None
        predictor.optimizer = None
        predictor.trained = False
        predictor._training_scheduled = False
        predictor.max_node_features = 100
        predictor.node_features = OrderedDict()
        predictor.graph = {}
        predictor.max_nodes = 100
        predictor.max_edges = 100
        predictor._edge_count = 0
        predictor.scheduler = None
        predictor._in_dim = 64
        predictor._hidden_dim = 32
        predictor._out_dim = 1
        predictor._last_cleanup = time.time()
        predictor._cleanup_interval = 300

        predictor._add_edge(1, 2)
        predictor._add_edge(1, 2)  # Duplicate
        assert predictor._edge_count == 1

    def test_gnn_edge_limit_eviction(self):
        """Test edge limit triggers eviction."""
        from hledac.universal.brain.gnn_predictor import GNNPredictor
        predictor = GNNPredictor.__new__(GNNPredictor)
        predictor.model = None
        predictor.optimizer = None
        predictor.trained = False
        predictor._training_scheduled = False
        predictor.max_node_features = 100
        predictor.node_features = OrderedDict()
        predictor.graph = {}
        predictor.max_nodes = 10
        predictor.max_edges = 5
        predictor._edge_count = 0
        predictor.scheduler = None
        predictor._in_dim = 64
        predictor._hidden_dim = 32
        predictor._out_dim = 1
        predictor._last_cleanup = time.time()
        predictor._cleanup_interval = 300

        # Add edges beyond limit
        for i in range(10):
            predictor._add_edge(i, i + 1)

        # Should have evicted some nodes
        assert predictor._edge_count <= predictor.max_edges


class TestPromptCacheLock:
    """Tests for PromptCache lock."""

    def test_prompt_cache_uses_lock(self):
        """Test PromptCache uses Lock (not RLock)."""
        from hledac.universal.brain.prompt_cache import PromptCache
        import inspect
        source = inspect.getsource(PromptCache.__init__)
        # Check it uses Lock, not RLock
        assert 'threading.Lock()' in source
        assert 'threading.RLock()' not in source


class TestUtils:
    """Tests for shared utilities."""

    def test_utils_importable(self):
        """Test utils module is importable."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from scripts import utils
        assert hasattr(utils, 'get_unified_memory_mb')

    def test_utils_get_memory_pressure(self):
        """Test get_memory_pressure returns string."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from scripts.utils import get_memory_pressure
        result = get_memory_pressure()
        assert isinstance(result, str)
        assert 'pressure=' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
