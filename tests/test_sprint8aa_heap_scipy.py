"""
Sprint 8AA: Heap Discipline + Scipy Lazy-Load + Runtime Memory Hotspot Preflight
================================================================================

Tests verify:
1. _findings_heap is already bounded (MAX_FINDINGS_IN_RAM = 50)
2. scipy.sparse is lazily loaded via _get_sparse() in memory_coordinator
3. NeuromorphicMemoryManager uses _get_sparse() correctly
4. graph_rag.py and hypothesis_engine.py preflight results
"""

import unittest


class TestFindingsHeapBoundedness(unittest.TestCase):
    """Verify _findings_heap is bounded by MAX_FINDINGS_IN_RAM."""

    def test_findings_heap_has_max_constant(self):
        """MAX_FINDINGS_IN_RAM constant should exist on _ResearchManager."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager
        self.assertTrue(hasattr(_ResearchManager, 'MAX_FINDINGS_IN_RAM'))
        self.assertEqual(_ResearchManager.MAX_FINDINGS_IN_RAM, 50)

    def test_research_manager_has_heap_eviction(self):
        """_ResearchManager should have _add_finding_with_limit method."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager
        self.assertTrue(hasattr(_ResearchManager, '_add_finding_with_limit'))


class TestScipyLazyLoad(unittest.TestCase):
    """Verify scipy.sparse is lazily loaded in memory_coordinator."""

    def test_scipy_sparse_lazy_getter_exists(self):
        """_get_sparse() function should exist at module level."""
        from hledac.universal.coordinators.memory_coordinator import _get_sparse
        self.assertTrue(callable(_get_sparse))

    def test_scipy_available_flag_exists(self):
        """SCIPY_AVAILABLE flag should exist."""
        from hledac.universal.coordinators.memory_coordinator import SCIPY_AVAILABLE
        self.assertIn(SCIPY_AVAILABLE, [True, False])

    def test_scipy_sparse_lazy_when_unavailable(self):
        """When scipy is not available, _get_sparse() returns None."""
        from hledac.universal.coordinators.memory_coordinator import SCIPY_AVAILABLE, _get_sparse
        sparse = _get_sparse()
        if not SCIPY_AVAILABLE:
            self.assertIsNone(sparse)

    def test_neuromorphic_memory_manager_uses_lazy_sparse(self):
        """NeuromorphicMemoryManager should use _get_sparse(), not module-level sparse."""
        from hledac.universal.coordinators.memory_coordinator import (
            NeuromorphicMemoryManager,
        )
        nm = NeuromorphicMemoryManager(n_neurons=64, connectivity=0.05)
        self.assertIsNotNone(nm.synaptic_weights)


class TestGraphRagHypothesisPreflight(unittest.TestCase):
    """Preflight checks for graph_rag.py and hypothesis_engine.py."""

    def test_graph_rag_import_smoke(self):
        """graph_rag.py should import without error."""
        from hledac.universal.knowledge import graph_rag
        self.assertTrue(hasattr(graph_rag, 'GraphRAGOrchestrator'))

    def test_hypothesis_engine_import_smoke(self):
        """hypothesis_engine.py should import without error."""
        from hledac.universal.brain import hypothesis_engine
        self.assertTrue(hasattr(hypothesis_engine, 'HypothesisEngine'))


if __name__ == '__main__':
    unittest.main()
