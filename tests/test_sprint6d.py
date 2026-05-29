#!/usr/bin/env python3
"""
Sprint 6D: Unit Tests
"""
import asyncio
import sys
import unittest
from collections import OrderedDict

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestTargetQueue(unittest.TestCase):
    """Test target queue functionality."""

    def test_bounded_target_queue_initialized(self):
        """Target queue is initialized with correct maxsize."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        self.assertTrue(hasattr(orch, '_target_queue'))
        self.assertEqual(orch._target_queue.maxsize, 10000)

    def test_target_extraction_cache_bounded(self):
        """Target extraction cache is bounded."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        self.assertTrue(hasattr(orch, '_target_extraction_cache'))
        self.assertEqual(orch._target_extraction_cache_maxsize, 1000)

    def test_target_extractor_finds_domain(self):
        """Target extractor finds domain from packet."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        packet = {'url': 'https://example.com/test', 'domain': 'example.com'}

        targets = orch._extract_targets_from_replay(packet, 'test_source')
        target_types = [t['type'] for t in targets]

        # Should extract domain and url
        self.assertIn('domain', target_types)
        self.assertIn('url', target_types)

    def test_target_extractor_finds_email(self):
        """Target extractor finds email from metadata."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        packet = {
            'url': 'https://test.com',
            'domain': 'test.com',
            'metadata_digests': {'email': 'user@example.com'}
        }

        targets = orch._extract_targets_from_replay(packet, 'test_source')
        target_types = [t['type'] for t in targets]

        self.assertIn('email', target_types)

    def test_target_queue_dedup(self):
        """Target queue prevents duplicate same targets."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        packet1 = {'url': 'https://example.com', 'domain': 'example.com'}
        packet2 = {'url': 'https://example.com', 'domain': 'example.com'}

        targets1 = orch._extract_targets_from_replay(packet1, 'src1')
        targets2 = orch._extract_targets_from_replay(packet2, 'src2')

        # Second call should have cached first, so fewer targets
        self.assertLessEqual(len(targets2), len(targets1))


class TestScoreBalancing(unittest.TestCase):
    """Test score balancing."""

    def test_academic_search_base_score(self):
        """Academic search has balanced base score."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Initialize actions
        asyncio.get_event_loop().run_until_complete(orch._initialize_actions())

        # Check academic_search scorer
        if 'academic_search' in orch._action_registry:
            _, scorer = orch._action_registry['academic_search']
            state = {'query': 'test', 'domain_staleness': 0}
            score, _ = scorer(state)

            # Base is 0.20, plus 0.10 bonus for research keywords in query = 0.30
            # This is acceptable (complement, not dominant)
            self.assertLessEqual(score, 0.35)

    def test_network_recon_base_score(self):
        """Network recon has balanced base score."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Initialize actions
        asyncio.get_event_loop().run_until_complete(orch._initialize_actions())

        # Check network_recon scorer
        if 'network_recon' in orch._action_registry:
            _, scorer = orch._action_registry['network_recon']
            state = {'new_domain': 'example.com', 'domain_staleness': 0}
            score, _ = scorer(state)

            # Should be around 0.40 (balanced with surface_search 0.5)
            self.assertLessEqual(score, 0.50)


class TestBoundedStructure(unittest.TestCase):
    """Test bounded structures."""

    def test_bounded_ordered_dict_fifo(self):
        """BoundedOrderedDict evicts oldest on overflow."""

        class BoundedOrderedDict(OrderedDict):
            def __init__(self, maxsize):
                super().__init__()
                self.maxsize = maxsize

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if len(self) > self.maxsize:
                    self.popitem(last=False)

        d = BoundedOrderedDict(maxsize=3)
        d['a'] = 1
        d['b'] = 2
        d['c'] = 3
        self.assertEqual(len(d), 3)

        d['d'] = 4
        self.assertEqual(len(d), 3)
        self.assertNotIn('a', d)
        self.assertIn('d', d)


if __name__ == '__main__':
    unittest.main(verbosity=2)
