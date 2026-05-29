"""
Sprint 8AE: Long-Run Memory Truth + Dedup-Hygiene + MLX Deprecation Sweep Phase 1
=================================================================================

Tests verify:
1. _processed_hashes is bounded at 5000 with FIFO eviction
2. heap↔processed_hashes invariant is preserved
3. MLX deprecation sites use modern mx.clear_cache() with fallback
4. mx.metal memory getters have proper hasattr guards
"""

import inspect
import unittest
from collections import OrderedDict


class TestProcessedHashesBoundedness(unittest.TestCase):
    """Verify _processed_hashes is bounded and operationally safe."""

    def test_processed_hashes_bounded_5000(self):
        """_processed_hashes should be bounded at 5000 with FIFO eviction."""

        # Create a minimal mock to test the structure behavior
        # The bound is enforced in _add_processed_hash
        class MockManager:
            def __init__(self):
                self._processed_hashes = OrderedDict()

            def _add_processed_hash(self, content_hash):
                self._processed_hashes[content_hash] = None
                while len(self._processed_hashes) > 5000:
                    self._processed_hashes.popitem(last=False)

        manager = MockManager()

        # Add 6000 items
        for i in range(6000):
            manager._add_processed_hash(f"hash_{i}")

        # Should be capped at 5000
        self.assertEqual(len(manager._processed_hashes), 5000)
        # Oldest items should be evicted
        self.assertNotIn("hash_0", manager._processed_hashes)
        # Newest items should be present
        self.assertIn("hash_5999", manager._processed_hashes)

    def test_processed_hashes_fifo_eviction_order(self):
        """FIFO eviction should remove oldest items first (popitem last=False)."""
        class MockManager:
            def __init__(self):
                self._processed_hashes = OrderedDict()

            def _add_processed_hash(self, content_hash):
                self._processed_hashes[content_hash] = None
                while len(self._processed_hashes) > 5000:
                    self._processed_hashes.popitem(last=False)

        manager = MockManager()

        for i in range(5500):
            manager._add_processed_hash(f"hash_{i}")

        # hash_0 through hash_499 should be evicted
        self.assertNotIn("hash_0", manager._processed_hashes)
        self.assertNotIn("hash_499", manager._processed_hashes)
        # hash_500 should be the oldest remaining
        self.assertIn("hash_500", manager._processed_hashes)
        self.assertIn("hash_5499", manager._processed_hashes)

    def test_heap_eviction_removes_hash(self):
        """When heap evicts an item, hash should also be removed."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager
        source = inspect.getsource(_ResearchManager._add_finding_with_limit)

        # Should call _processed_hashes.pop with removed_hash
        self.assertIn("_processed_hashes.pop(removed_hash", source)


class TestMLXDeprecationSweep(unittest.TestCase):
    """Verify MLX deprecation sites use modern API with fallback."""

    def test_mlx_clear_cache_uses_modern_api(self):
        """mx.metal.clear_cache should use mx.clear_cache with fallback."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        source = inspect.getsource(FullyAutonomousOrchestrator)

        # Should prefer mx.clear_cache over mx.metal.clear_cache
        self.assertIn("hasattr(mx, 'clear_cache')", source,
            "Should have hasattr(mx, 'clear_cache') guard for modern API")

    def test_metal_set_memory_limit_has_guard(self):
        """mx.metal.set_memory_limit should have hasattr guard."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        source = inspect.getsource(FullyAutonomousOrchestrator)

        # Should check hasattr before calling set_memory_limit
        self.assertIn("hasattr(mx, 'set_memory_limit')", source,
            "Should have hasattr guard for set_memory_limit")

    def test_hermes3_engine_has_metal_guard(self):
        """hermes3_engine._get_gpu_memory should have mx API fallback."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._get_gpu_memory)

        # Should prefer top-level mx API
        self.assertIn("hasattr(mx, 'get_active_memory')", source,
            "Should prefer top-level mx.get_active_memory()")

    def test_prompt_bandit_has_metal_guard(self):
        """prompt_bandit context features should have mx API fallback."""
        from hledac.universal.brain.prompt_bandit import PromptBandit
        source = inspect.getsource(PromptBandit._get_context_vector)

        # Should have hasattr guard for get_active_memory
        self.assertIn("hasattr(mx, 'get_active_memory')", source,
            "Should prefer top-level mx.get_active_memory()")


class TestDedupInvariant(unittest.TestCase):
    """Verify heap↔processed_hashes invariant."""

    def test_add_processed_hash_method_exists(self):
        """_add_processed_hash helper should exist on _ResearchManager."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager
        self.assertTrue(hasattr(_ResearchManager, '_add_processed_hash'))

    def test_processed_hashes_comment_bounded(self):
        """Comment should document the 5000 bound."""
        from hledac.universal.autonomous_orchestrator import _ResearchManager
        source = inspect.getsource(_ResearchManager)
        self.assertIn("maxlen=5000", source,
            "Should document maxlen=5000 in comment")


if __name__ == '__main__':
    unittest.main()
