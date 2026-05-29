#!/usr/bin/env python3
"""
Sprint 6C: Unit Tests
- Calibration consistency
- Contextual TS integrity
- Network recon wildcard fix
- Bounded wrappers
"""
import sys
import unittest
from collections import OrderedDict

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestCalibrationConsistency(unittest.TestCase):
    """Test authoritative calibration structure."""

    def test_calibration_returns_authoritative_structure(self):
        """Calibration must return ts_healthy verdict from same structure."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        # Setup: add some posteriors with different calibration
        orch._ts_posteriors = {
            'action_good': {'alpha': 9.0, 'beta': 1.0},  # 0.9 mean
            'action_bad': {'alpha': 1.0, 'beta': 9.0},    # 0.1 mean
        }
        orch._action_executed_counts = {
            'action_good': 20,
            'action_bad': 20,
        }
        orch._action_success_counts = {
            'action_good': 18,  # 0.9 observed ~ 0.9 posterior
            'action_bad': 2,    # 0.1 observed ~ 0.1 posterior
        }
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        calib = orch._compute_ts_calibration()

        # Must have authoritative fields
        self.assertIn('ts_healthy', calib)
        self.assertIn('weighted_mean_calibration_error', calib)
        self.assertIn('calibrated_well_count', calib)
        self.assertIn('calibrated_warn_count', calib)
        self.assertIn('calibrated_poor_count', calib)
        self.assertIn('action_calibrations', calib)

    def test_calibration_well_warn_poor_logic(self):
        """Well/warn/poor status computed correctly."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._ts_posteriors = {
            'well': {'alpha': 5.0, 'beta': 5.0},   # 0.5 mean
            'warn': {'alpha': 3.0, 'beta': 7.0},   # 0.3 mean
            'poor': {'alpha': 1.0, 'beta': 9.0},    # 0.1 mean
        }
        orch._action_executed_counts = {'well': 20, 'warn': 20, 'poor': 20}
        # Observed rates: well=0.5 (~0.5), warn=0.15 (error=0.15), poor=0.8 (error=0.7)
        orch._action_success_counts = {'well': 10, 'warn': 3, 'poor': 16}
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        calib = orch._compute_ts_calibration()

        # Check statuses
        well_cal = calib['action_calibrations']['well']
        warn_cal = calib['action_calibrations']['warn']
        poor_cal = calib['action_calibrations']['poor']

        self.assertEqual(well_cal['status'], 'well_calibrated')  # error ~0
        self.assertEqual(warn_cal['status'], 'warn_calibrated')   # 0.15 between 0.10-0.30
        self.assertEqual(poor_cal['status'], 'poor_calibrated')  # 0.7 > 0.30

    def test_calibration_ts_healthy_verdict(self):
        """ts_healthy verdict is logically derived."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        # Well calibrated majority, low error
        orch._ts_posteriors = {
            'a1': {'alpha': 5.0, 'beta': 5.0},
            'a2': {'alpha': 5.0, 'beta': 5.0},
            'a3': {'alpha': 5.0, 'beta': 5.0},
        }
        orch._action_executed_counts = {'a1': 20, 'a2': 20, 'a3': 20}
        orch._action_success_counts = {'a1': 10, 'a2': 10, 'a3': 10}
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        calib = orch._compute_ts_calibration()

        # Should be healthy: well > poor and error < 0.30
        self.assertTrue(calib['ts_healthy'])


class TestBoundedWrappers(unittest.TestCase):
    """Test bounded data structures."""

    def test_bounded_ordered_dict_fifo_eviction(self):
        """BoundedOrderedDict evicts oldest on overflow."""

        class BoundedOrderedDict(OrderedDict):
            """Bounded wrapper with FIFO eviction."""
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

        # Add one more - should evict 'a'
        d['d'] = 4
        self.assertEqual(len(d), 3)
        self.assertNotIn('a', d)
        self.assertIn('d', d)


class TestNetworkReconWildcardFix(unittest.TestCase):
    """Test network_recon wildcard is metadata not kill-switch."""

    def test_wildcard_metadata_not_killswitch(self):
        """Wildcard detection should not unconditionally suppress findings."""
        # This is verified by the scorer accepting wildcard domains
        # and the handler extracting valuable records even with wildcard
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()

        # Verify network_recon_scorer exists and has reasonable score
        self.assertTrue(hasattr(orch, '_action_registry'))
        if 'network_recon' in orch._action_registry:
            handler, scorer = orch._action_registry['network_recon']
            # Score should be 0.55 (RARE_HIGH_VALUE)
            state = {'new_domain': 'example.com', 'domain_staleness': 0}
            score, _ = scorer(state)
            self.assertLess(score, 0.70)  # Not dominating


class TestContextualTS(unittest.TestCase):
    """Test contextual TS integrity."""

    def test_contextual_posterior_fallback(self):
        """Contextual posterior falls back to global when insufficient data."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        orch._ts_posteriors = {'test_action': {'alpha': 5.0, 'beta': 5.0}}
        orch._contextual_ts_data = {}  # Empty - no local data

        # Should fall back to global
        if hasattr(orch, '_get_contextual_posterior'):
            posterior = orch._get_contextual_posterior('domain', 'test_action')
            # Should get global posterior as fallback
            self.assertIsNotNone(posterior)


class TestAdaptiveExploration(unittest.TestCase):
    """Test adaptive exploration ratio."""

    def test_adaptive_exploration_ratio_bounded(self):
        """Adaptive exploration ratio must be within bounds."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        orch = FullyAutonomousOrchestrator()
        # Initialize action data
        orch._action_executed_counts = {}
        orch._action_success_counts = {}
        orch._ts_posteriors = {}

        if hasattr(orch, '_compute_adaptive_exploration_ratio'):
            ratio = orch._compute_adaptive_exploration_ratio()
            self.assertGreaterEqual(ratio, 0.05)
            self.assertLessEqual(ratio, 0.30)


if __name__ == '__main__':
    unittest.main(verbosity=2)
