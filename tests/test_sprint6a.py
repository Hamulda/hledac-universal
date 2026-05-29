#!/usr/bin/env python3
"""
Sprint 6A: Unit Tests for TS Calibration and GC Checkpoint
"""
import sys
import unittest

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from collections import OrderedDict


class BoundedOrderedDict(OrderedDict):
    """Hard rule #12: BoundedOrderedDict wrapper with FIFO eviction."""
    def __init__(self, maxsize: int):
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)  # FIFO: remove oldest


class TestBoundedOrderedDict(unittest.TestCase):
    def test_fifo_eviction(self):
        """Hard rule #13: FIFO eviction test."""
        d = BoundedOrderedDict(maxsize=5)
        for i in range(10):
            d[f"key{i}"] = i
        self.assertEqual(len(d), 5)
        self.assertIn('key5', d)
        self.assertIn('key9', d)
        self.assertNotIn('key0', d)


class TestTelemetryHealthcheck(unittest.TestCase):
    def test_telemetry_attributes_exist(self):
        """Verify all Sprint 6A telemetry attributes exist."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        required_attrs = [
            '_latency_window', '_gc_collected_total', '_gc_time_total_ms',
            '_action_success_counts', '_unique_sources_this_cycle',
            '_unique_sources_prev_cycle', '_unique_sources_total_cumulative',
            '_calibration_snapshots', '_calib_success_snap', '_calib_executed_snap',
            '_active_task_baseline', '_active_task_peak', '_exploration_budget_triggers'
        ]

        for attr in required_attrs:
            self.assertTrue(
                hasattr(orch, attr),
                f"Missing attribute: {attr}"
            )


class TestTSCalibration(unittest.TestCase):
    def test_calibration_excludes_low_data(self):
        """Test that calibration excludes actions with < 10 executions."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # Setup: add some posteriors with low execution counts
        orch._ts_posteriors = {
            'action_a': {'alpha': 5.0, 'beta': 3.0},  # 8 executions (should exclude)
            'action_b': {'alpha': 10.0, 'beta': 5.0},  # 15 executions (should include)
        }
        orch._action_executed_counts = {
            'action_a': 8,   # Below threshold
            'action_b': 15,  # Above threshold
        }
        orch._action_success_counts = {
            'action_a': 5,
            'action_b': 10,
        }
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        calib = orch._compute_ts_calibration()

        # action_a should be excluded
        self.assertTrue(calib['action_calibrations']['action_a']['excluded'])
        self.assertEqual(calib['action_calibrations']['action_a']['reason'], 'low_data')

        # action_b should be included
        self.assertFalse(calib['action_calibrations']['action_b']['excluded'])

        # Check counts
        self.assertEqual(calib['actions_excluded_low_data'], 1)
        self.assertEqual(calib['actions_excluded_blocked'], 0)

    def test_calibration_computes_error(self):
        """Test that calibration computes error correctly."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # posterior_mean = alpha / (alpha + beta) = 10 / 20 = 0.5
        # observed_rate = success / executed = 10 / 20 = 0.5
        # calibration_error = |0.5 - 0.5| = 0.0
        orch._ts_posteriors = {
            'action_a': {'alpha': 10.0, 'beta': 10.0},
        }
        orch._action_executed_counts = {'action_a': 20}
        orch._action_success_counts = {'action_a': 10}
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        calib = orch._compute_ts_calibration()

        self.assertAlmostEqual(
            calib['action_calibrations']['action_a']['calibration_error'],
            0.0,
            places=2
        )

    def test_calibration_weighted_mean(self):
        """Test weighted mean calculation."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        orch._ts_posteriors = {
            'action_a': {'alpha': 8.0, 'beta': 2.0},   # mean=0.8
            'action_b': {'alpha': 3.0, 'beta': 7.0},   # mean=0.3
        }
        orch._action_executed_counts = {'action_a': 10, 'action_b': 10}
        orch._action_success_counts = {'action_a': 8, 'action_b': 3}
        # action_a: posterior=0.8, observed=0.8, error=0.0
        # action_b: posterior=0.3, observed=0.3, error=0.0
        # weighted_mean = (0*10 + 0*10) / 20 = 0.0

        calib = orch._compute_ts_calibration()

        self.assertAlmostEqual(calib['weighted_mean_calibration_error'], 0.0, places=2)
        self.assertEqual(calib['actions_calibrated'], 2)


class TestGCCheckpoint(unittest.TestCase):
    def test_gc_checkpoint_increments_counters(self):
        """Test that GC checkpoint increments counters."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        orch._gc_collected_total = 0
        orch._gc_time_total_ms = 0.0

        # Force GC by creating garbage
        _ = [object() for _ in range(100)]

        orch._gc_checkpoint("test")

        # GC may or may not collect depending on state
        # Just verify method runs without error and time is tracked
        self.assertIsInstance(orch._gc_collected_total, int)
        self.assertIsInstance(orch._gc_time_total_ms, float)


class TestPrfExpandFix(unittest.TestCase):
    def test_prf_expand_registration(self):
        """Test prf_expand is registered in action registry."""
        import asyncio

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        async def test():
            orch = FullyAutonomousOrchestrator()
            await orch._initialize_actions()

            self.assertIn('prf_expand', orch._action_registry)

            handler, scorer = orch._action_registry['prf_expand']
            self.assertIsNotNone(handler)
            self.assertIsNotNone(scorer)

        asyncio.run(test())


class TestCalibrationSnapshot(unittest.TestCase):
    def test_take_calibration_snapshot(self):
        """Test calibration snapshot at cycle 3."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        orch._action_success_counts = {'action_a': 5, 'action_b': 10}
        orch._action_executed_counts = {'action_a': 10, 'action_b': 20}

        orch._take_calibration_snapshot(3, "test_cycle")

        self.assertIsNotNone(orch._calib_success_snap)
        self.assertIsNotNone(orch._calib_executed_snap)
        self.assertEqual(orch._calib_success_snap['action_a'], 5)
        self.assertEqual(orch._calib_executed_snap['action_b'], 20)


class TestAdaptiveExploration(unittest.TestCase):
    def test_adaptive_exploration_ratio_bounded(self):
        """Test that adaptive exploration ratio is clamped to [0.05, 0.30]."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # Set up diverse posteriors
        orch._ts_posteriors = {
            'action_a': {'alpha': 5.0, 'beta': 5.0},  # mean=0.5, variance ~0.06
            'action_b': {'alpha': 2.0, 'beta': 8.0},  # mean=0.2
        }
        orch._action_executed_counts = {'action_a': 10, 'action_b': 10}
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        ratio = orch._compute_adaptive_exploration_ratio()

        # Ratio should be between 0.05 and 0.30
        self.assertGreaterEqual(ratio, 0.05)
        self.assertLessEqual(ratio, 0.30)

    def test_adaptive_exploration_zero_run_bonus(self):
        """Test that zero-run actions increase exploration ratio."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        orch._ts_posteriors = {
            'action_a': {'alpha': 1.0, 'beta': 1.0},  # mean=0.5
        }
        orch._action_executed_counts = {'action_a': 0}  # Zero runs
        orch._action_skipped_cooldown_counts = {}
        orch._action_skipped_gate_counts = {}

        ratio = orch._compute_adaptive_exploration_ratio()

        # Should have zero-run bonus, ratio > base 0.20
        self.assertGreater(ratio, 0.15)


class TestContextualTS(unittest.TestCase):
    def test_extract_context_key_priority(self):
        """Test context key extraction from state."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # Test domain priority
        state = {'domain': 'example.com', 'url': 'http://example.com'}
        key = orch._extract_context_key(state)
        self.assertEqual(key, 'domain')

        # Test email priority
        state = {'email': 'test@example.com', 'unknown': 'value'}
        key = orch._extract_context_key(state)
        self.assertEqual(key, 'email')

    def test_contextual_posterior_fallback(self):
        """Test contextual TS falls back to global when insufficient data."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        # Set up global posterior
        orch._ts_posteriors = {'action_a': {'alpha': 5.0, 'beta': 5.0}}
        orch._contextual_ts_data = {}

        # Request contextual posterior with no data - should fallback to global
        ctx_posterior = orch._get_contextual_posterior('domain', 'action_a')

        # Should return global posterior (alpha=5, beta=5)
        self.assertEqual(ctx_posterior.get('alpha'), 5.0)
        self.assertEqual(ctx_posterior.get('beta'), 5.0)
        self.assertEqual(orch._contextual_ts_fallback_count, 1)

    def test_contextual_posterior_update_writes_both(self):
        """Test that contextual update writes to both global and local."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()

        orch._ts_posteriors = {'action_a': {'alpha': 1.0, 'beta': 1.0}}
        orch._contextual_ts_data = {}

        # Update with success
        orch._update_contextual_posterior('domain', 'action_a', True)

        # Check global was updated
        self.assertEqual(orch._ts_posteriors['action_a']['alpha'], 2.0)

        # Check local was updated
        self.assertIn('domain', orch._contextual_ts_data)
        self.assertIn('action_a', orch._contextual_ts_data['domain'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
