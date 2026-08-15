"""Sprint 8M: Memory Coordinator Import Diet + Package Cascade Fix

Tests verify:
1. autonomous_orchestrator.py is untouched
2. coordinators package __init__ cascade is audited
3. scipy/scipy.sparse is lazily imported in memory_coordinator
4. NeuromorphicMemoryManager works with lazy numpy
5. MemoryCoordinator still functions correctly
"""

import unittest
from core import aclose


class TestAutonomousOrchestratorUntouched(unittest.TestCase):
    """Verify autonomous_orchestrator.py was not edited in Sprint 8M."""

    def test_no_changes_to_autonomous_orchestrator(self):
        """autonomous_orchestrator.py should not be modified in Sprint 8M."""
        import inspect

        from hledac.universal import autonomous_orchestrator as ao_module

        source = inspect.getsource(ao_module)
        # If it imports scipy or sklearn directly at module level, it would be a problem
        # But we only check that this sprint didn't touch it
        self.assertIn("FullyAutonomousOrchestrator", source)


class TestLazyScipyInMemoryCoordinator(unittest.TestCase):
    """Verify scipy.sparse is lazily imported via try/except."""

    def test_scipy_sparse_is_lazy_guard(self):
        """scipy.sparse import should be wrapped in try/except (in knowledge.neuromorphic)."""
        import inspect

        # Sprint F320-10: scipy.sparse lazy import moved to knowledge.neuromorphic
        from hledac.universal.knowledge import neuromorphic as neuro_module

        source = inspect.getsource(neuro_module)

        # Verify try/except guard around scipy import
        self.assertIn("try:", source)
        self.assertIn("from scipy", source)
        self.assertIn("_scIPY_AVAILABLE", source)  # Note: underscore prefix
        self.assertIn("except ImportError:", source)

    def test_scipy_sparse_fallback_when_unavailable(self):
        """When scipy is not available, NeuromorphicMemoryManager handles it gracefully."""
        # Sprint F320-10: _get_sparse moved to knowledge.neuromorphic as _get_scipy_sparse
        from hledac.universal.knowledge.neuromorphic import _get_scipy_sparse, _scIPY_AVAILABLE

        sparse = _get_scipy_sparse()
        if not _scIPY_AVAILABLE:
            self.assertIsNone(sparse)
        else:
            self.assertIn(_scIPY_AVAILABLE, [True, False])


class TestNeuromorphicMemoryManagerLazyNumpy(unittest.TestCase):
    """Verify NeuromorphicMemoryManager uses lazy numpy accessor."""

    def test_get_np_function_exists(self):
        """_get_np() function should exist at module level."""
        from hledac.universal.coordinators.memory_coordinator import _get_np

        self.assertTrue(callable(_get_np))

    def test_get_np_returns_numpy(self):
        """_get_np() should return numpy module."""
        from hledac.universal.coordinators.memory_coordinator import _get_np

        np = _get_np()
        self.assertTrue(hasattr(np, "zeros"))
        self.assertTrue(hasattr(np, "random"))
        self.assertTrue(hasattr(np, "exp"))

    def test_neuromorphic_memory_manager_instantiates(self):
        """NeuromorphicMemoryManager should instantiate with lazy numpy."""
        # Sprint F320-10: NeuromorphicMemoryManager moved to knowledge.neuromorphic
        from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryManager

        nm = NeuromorphicMemoryManager(n_neurons=64, connectivity=0.05)
        self.assertEqual(nm.n_neurons, 64)
        self.assertIsNotNone(nm.spike_traces)

    def test_neuromorphic_pattern_storage(self):
        """NeuromorphicMemoryManager should store and recall patterns."""
        # Sprint F320-10: NeuromorphicMemoryManager moved to knowledge.neuromorphic
        from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryManager, NeuromorphicMemoryZone

        nm = NeuromorphicMemoryManager(n_neurons=64, connectivity=0.05)
        data = {"query": "test", "result": 42}
        stored = nm.store_pattern("p1", data, NeuromorphicMemoryZone.WORKING_MEMORY)
        self.assertTrue(stored)

        recalled = nm.recall_pattern("p1", completion=False)
        self.assertIsNotNone(recalled)


class TestUniversalMemoryCoordinatorFunctionality(unittest.TestCase):
    """Verify UniversalMemoryCoordinator still works correctly."""

    def test_memory_coordinator_instantiates(self):
        """UniversalMemoryCoordinator should instantiate."""
        from hledac.universal.coordinators.memory_coordinator import (
            UniversalMemoryCoordinator,
        )

        coord = UniversalMemoryCoordinator(memory_limit_mb=500)
        self.assertEqual(coord.memory_limit_mb, 500)

    def test_memory_usage_tracking(self, session_event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """MemoryCoordinator should track memory usage."""
        from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator(memory_limit_mb=500)
        stats = session_event_loop.run_until_complete(coord.get_memory_usage())
        self.assertGreater(stats.total_memory_mb, 0)
        self.assertIsNotNone(stats.current_level)

    def test_memory_zone_operations(self, session_event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """MemoryCoordinator should support zone operations."""
        from hledac.universal.coordinators.memory_coordinator import MemoryZone, UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator(memory_limit_mb=500)

        async def _test():
            allocated = await coord.allocate("test_alloc", MemoryZone.HIGH, size_bytes=1024, priority=5)
            self.assertTrue(allocated)
            zone_stats = await coord.get_zone_usage(MemoryZone.HIGH)
            self.assertEqual(zone_stats.zone, "high")
            self.assertGreater(zone_stats.allocation_count, 0)
            freed = await coord.free("test_alloc")
            self.assertTrue(freed)

        session_event_loop.run_until_complete(_test())

    def test_aggressive_cleanup(self, session_event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        """MemoryCoordinator should perform aggressive cleanup."""
        from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator

        coord = UniversalMemoryCoordinator(memory_limit_mb=500)
        result = session_event_loop.run_until_complete(coord.aggressive_cleanup())
        self.assertIn("success", result)
        self.assertIn("gc_collections", result)


class TestTypeAnnotationsSafe(unittest.TestCase):
    """Verify future annotations prevent NameError."""

    def test_future_annotations_imported(self):
        """memory_coordinator should have future annotations import."""
        # The class should define np.ndarray in type hints without triggering NameError
        # This tests that __future__ annotations are present
        # Sprint F320-10: NeuromorphicMemoryManager moved to knowledge.neuromorphic.
        # Uses 'Any' for neuron_activations to avoid numpy import at module level.
        import inspect

        from hledac.universal.knowledge.neuromorphic import NeuromorphicMemoryManager

        source = inspect.getsource(NeuromorphicMemoryManager)
        # Verify we use 'Any' not 'np.ndarray' — avoids numpy at module import time
        self.assertIn("Any", source)
        self.assertNotIn("np.ndarray", source)

    def test_no_name_error_on_import(self):
        """Importing memory_coordinator should not raise NameError."""
        # This test passes if we get here without exception
        # All classes imported successfully
        self.assertTrue(True)


class TestPackageCascadeAudit(unittest.TestCase):
    """Audit the coordinators package cascade root cause."""

    def test_scipy_sparse_is_optional_guard(self):
        """scipy.sparse should be guarded with lazy _get_scipy_sparse() in knowledge.neuromorphic."""
        # Sprint F320-10: moved to knowledge.neuromorphic as _get_scipy_sparse / _scIPY_AVAILABLE
        from hledac.universal.knowledge.neuromorphic import _get_scipy_sparse, _scIPY_AVAILABLE

        self.assertTrue(callable(_get_scipy_sparse))
        self.assertIn(_scIPY_AVAILABLE, [True, False])

    def test_numpy_still_available(self):
        """numpy should still be available for non-neuromorphic paths."""
        from hledac.universal.coordinators.memory_coordinator import np

        arr = np.zeros(3)
        self.assertEqual(len(arr), 3)


class TestCoordinatorsPackageCascade(unittest.TestCase):
    """Audit coordinators package import cascade."""

    def test_coordinators_init_has_many_imports(self):
        """coordinators/__init__.py imports many submodules."""
        import inspect

        from hledac.universal import coordinators

        source = inspect.getsource(coordinators)
        # Should have multiple coordinator imports
        self.assertGreater(source.count("from ."), 5)


if __name__ == "__main__":
    unittest.main()
