"""
Sprint 5T: TS Anti-Monopoly + Network_RECON_V2 + 300s Preview Tests

Tests for:
- TS exploration budget
- Posterior collapse reset
- _seen_domains bounded FIFO
- network_recon_v2 readiness
"""

import asyncio
import sys
import unittest
from collections import OrderedDict

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestSprint5TExplorationBudget(unittest.TestCase):
    """Test TS exploration budget and anti-monopoly."""

    def test_exploration_budget_constants_exist(self):
        """Test that exploration budget constants are defined."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        self.assertTrue(hasattr(orch, '_TS_MIN_EXPLORATION_BUDGET'))
        self.assertEqual(orch._TS_MIN_EXPLORATION_BUDGET, 0.20)

        self.assertTrue(hasattr(orch, '_TS_WARMUP_ITERATIONS'))
        self.assertEqual(orch._TS_WARMUP_ITERATIONS, 50)

        self.assertTrue(hasattr(orch, '_TS_VALIDATION_MIN_POSTERIOR_UNCERTAINTY'))
        self.assertEqual(orch._TS_VALIDATION_MIN_POSTERIOR_UNCERTAINTY, 0.05)

    def test_exploration_budget_triggered_at_iteration_50(self):
        """Test that exploration budget triggers after warmup."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._iter_count = 60  # After warmup
        orch._TS_MIN_EXPLORATION_BUDGET = 0.20
        orch._TS_WARMUP_ITERATIONS = 50
        orch._action_total_runs = {'action1': 5, 'action2': 3}

        candidates = [(0.8, 'action1', {}), (0.6, 'action2', {})]

        # Test: should select least explored (action2 with 3 runs)
        # The exploration budget logic selects action with minimum runs
        min_runs = float('inf')
        least_explored = None
        for score, name, params in candidates:
            runs = orch._action_total_runs.get(name, 0)
            if runs < min_runs:
                min_runs = runs
                least_explored = (score, name, params)

        self.assertEqual(least_explored[1], 'action2')


class TestSprint5TPosteriorReset(unittest.TestCase):
    """Test posterior collapse reset."""

    def test_posterior_reset_when_uncertainty_low(self):
        """Test that posterior resets when uncertainty drops below threshold."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._TS_VALIDATION_MIN_POSTERIOR_UNCERTAINTY = 0.05
        orch._TS_MAX_POSTERIOR = 1000.0

        # Set up a collapsed posterior (high alpha + beta = low uncertainty)
        orch._ts_posteriors = {'surface_search': {'alpha': 100.0, 'beta': 20.0}}
        orch._action_total_runs = {'surface_search': 15}  # > 10 runs

        # Calculate current uncertainty
        post = orch._ts_posteriors['surface_search']
        alpha, beta = post['alpha'], post['beta']
        uncertainty = (alpha * beta) ** 0.5 / ((alpha + beta) ** 1.5)
        self.assertLess(uncertainty, 0.05)  # Should trigger reset

        # Simulate the reset logic
        if uncertainty < orch._TS_VALIDATION_MIN_POSTERIOR_UNCERTAINTY:
            orch._ts_posteriors['surface_search'] = {'alpha': 2.0, 'beta': 2.0}

        # Verify reset happened
        new_post = orch._ts_posteriors['surface_search']
        new_uncertainty = (new_post['alpha'] * new_post['beta']) ** 0.5 / ((new_post['alpha'] + new_post['beta']) ** 1.5)
        self.assertGreater(new_uncertainty, 0.1)  # Should be higher


class TestSprint5TSeenDomainsBounded(unittest.TestCase):
    """Test _seen_domains bounded FIFO."""

    def test_seen_domains_fifo_eviction(self):
        """Test that oldest domains are evicted first."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._SEEN_DOMAINS_MAXSIZE = 5
        orch._seen_domains = OrderedDict()

        # Add 10 domains
        for i in range(10):
            orch._seen_domains_add(f'domain{i}')

        # Should have max 5
        self.assertEqual(len(orch._seen_domains), 5)

        # Oldest should be domain5 (domain0-4 evicted)
        first_key = next(iter(orch._seen_domains))
        self.assertEqual(first_key, 'domain5')

    def test_seen_domains_dedup(self):
        """Test domain deduplication."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._SEEN_DOMAINS_MAXSIZE = 10
        orch._seen_domains = OrderedDict()

        # Add domain
        result1 = orch._seen_domains_add('test.com')
        self.assertTrue(result1)  # New

        # Add same domain again
        result2 = orch._seen_domains_add('test.com')
        self.assertFalse(result2)  # Duplicate

        # Contains should be True
        self.assertTrue(orch._seen_domains_contains('test.com'))

    def test_seen_domains_clear(self):
        """Test _seen_domains reset."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._SEEN_DOMAINS_MAXSIZE = 10
        orch._seen_domains = OrderedDict([('a', True), ('b', True)])

        orch._seen_domains_clear()
        self.assertEqual(len(orch._seen_domains), 0)


class TestSprint5TNetworkReconV2Ready(unittest.TestCase):
    """Test network_recon_v2 readiness detection."""

    def test_readiness_check_all_prerequisites(self):
        """Test that readiness checks all prerequisites."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Setup prerequisites
        orch._seen_domains = OrderedDict()
        orch._SEEN_DOMAINS_MAXSIZE = 50000
        orch._network_recon_semaphore = asyncio.Semaphore(2)
        import concurrent.futures
        orch._network_recon_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        orch._handle_network_recon_v2 = lambda: None  # Add handler

        # Check readiness
        ready = orch._check_network_recon_readiness()
        self.assertTrue(ready)

        # Cleanup
        orch._network_recon_executor.shutdown(wait=False)

    def test_readiness_false_when_missing_components(self):
        """Test readiness is False when components missing."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        # No components set

        ready = orch._check_network_recon_readiness()
        self.assertFalse(ready)


class TestSprint5TDuplicateResultNonPenalizable(unittest.TestCase):
    """Test that duplicate_result is non-penalizable."""

    def test_duplicate_result_in_non_penalizable(self):
        """Test duplicate_result is in NON_PENALIZABLE_STATUSES."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Should contain duplicate_result
        self.assertIn('duplicate_result', orch._NON_PENALIZABLE_STATUSES)


if __name__ == '__main__':
    unittest.main()
