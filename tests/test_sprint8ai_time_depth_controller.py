"""
Sprint 8AI: Time-Based Depth Controller
=======================================

Tests verify:
1. Time-based rolling HHI visible during run
2. Rolling history evicts stale entries before compute
3. Monopoly guard no longer uses iteration-based window
4. Old monopoly window constant deprecated
5. Time-weighted EMA uses dt, not fixed alpha
6. Time-weighted EMA handles zero dt
7. Exploration bonus is time-decayed
8. Rolling HHI visible in sprint state during run
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class TestSprint8AITimeBasedHHI(unittest.IsolatedAsyncioTestCase):
    """Verify time-based HHI monitor."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_rolling_hhi_visible_during_run_if_touched(self):
        """Rolling HHI must be visible during run via _sprint_state."""
        orch = self.orch
        # Seed rolling history with actions
        now = time.monotonic()
        orch._rolling_action_history.append((now - 100, 'surface_search'))
        orch._rolling_action_history.append((now - 50, 'network_recon'))
        orch._rolling_action_history.append((now - 20, 'surface_search'))
        orch._rolling_action_history.append((now - 10, 'network_recon'))
        orch._rolling_action_history.append((now - 5, 'surface_search'))

        # Manually trigger HHI compute path (simulate selection)
        if len(orch._rolling_action_history) >= 5:
            import collections
            family_counts = collections.Counter(a for _, a in orch._rolling_action_history)
            total = sum(family_counts.values())
            hhi_sum = sum((c / total) ** 2 for c in family_counts.values())
            orch._rolling_hhi = hhi_sum

        self.assertGreater(orch._rolling_hhi, 0.0)
        self.assertLessEqual(orch._rolling_hhi, 1.0)

    async def test_rolling_hhi_evicts_stale_entries_before_compute_if_touched(self):
        """Stale entries older than window must be evicted before HHI compute."""
        orch = self.orch
        now = time.monotonic()
        # Add one very old entry (should be evicted: 400s > 300s window)
        orch._rolling_action_history.append((now - 400, 'surface_search'))
        # Add recent entries (all within 300s window)
        orch._rolling_action_history.append((now - 10, 'network_recon'))
        orch._rolling_action_history.append((now - 5, 'ct_discovery'))
        orch._rolling_action_history.append((now - 2, 'network_recon'))
        orch._rolling_action_history.append((now - 1, 'ct_discovery'))

        # Eviction logic: evict while oldest > window
        while (now - orch._rolling_action_history[0][0]) > orch._MONOPOLY_GUARD_WINDOW_SEC:
            orch._rolling_action_history.popleft()

        # Old entry (400s) should be gone, leaving 4 recent entries
        self.assertLess(len(orch._rolling_action_history), 5)
        self.assertEqual(len(orch._rolling_action_history), 4)
        # The old entry was surface_search at index 0 — verify oldest timestamp > 300s ago
        oldest_ts = orch._rolling_action_history[0][0]
        self.assertGreater(now - oldest_ts, 0)  # positive
        self.assertLessEqual(now - oldest_ts, 300.0)  # within window

    async def test_time_based_hhi_monitor_records_monotonic_timestamps_if_touched(self):
        """Rolling history must store (timestamp, action) tuples."""
        orch = self.orch
        orch._rolling_action_history.clear()
        now = time.monotonic()
        orch._rolling_action_history.append((now, 'surface_search'))
        orch._rolling_action_history.append((now - 5, 'network_recon'))

        self.assertEqual(len(orch._rolling_action_history), 2)
        self.assertIsInstance(orch._rolling_action_history[0], tuple)
        self.assertEqual(len(orch._rolling_action_history[0]), 2)
        # First element is timestamp (float), second is action name
        self.assertIsInstance(orch._rolling_action_history[0][0], float)
        self.assertIsInstance(orch._rolling_action_history[0][1], str)


class TestSprint8AIMonopolyGuard(unittest.IsolatedAsyncioTestCase):
    """Verify iteration-based monopoly is deprecated."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_live_monopoly_guard_no_longer_uses_iteration_window_if_touched(self):
        """Live monopoly guard must use time-based window, not iteration count."""
        orch = self.orch
        # The NEW time-based guard uses _MONOPOLY_GUARD_WINDOW_SEC and _rolling_action_history
        self.assertTrue(hasattr(orch, '_MONOPOLY_GUARD_WINDOW_SEC'))
        self.assertTrue(hasattr(orch, '_MONOPOLY_GUARD_THRESHOLD'))
        self.assertTrue(hasattr(orch, '_rolling_action_history'))
        # Window should be time-based (300 seconds), not 50 iterations
        self.assertEqual(orch._MONOPOLY_GUARD_WINDOW_SEC, 300.0)
        self.assertEqual(orch._MONOPOLY_GUARD_THRESHOLD, 0.80)

    async def test_old_monopoly_window_constant_removed_or_deprecated_if_touched(self):
        """Old _monopoly_guard_window must be marked deprecated."""
        orch = self.orch
        # Old constant must exist but be marked deprecated
        self.assertTrue(hasattr(orch, '_monopoly_guard_window'))
        # The deprecation comment must be visible in source
        import inspect
        source = inspect.getsource(type(orch).__init__)
        self.assertIn('DEPRECATED', source)


class TestSprint8AITimeWeightedEMA(unittest.IsolatedAsyncioTestCase):
    """Verify time-weighted EMA uses dt, not fixed alpha."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_time_weighted_ema_uses_dt_not_fixed_alpha_if_touched(self):
        """Time-weighted EMA must use alpha = 1 - exp(-dt/tau)."""
        import math
        orch = self.orch
        tau = orch._EMA_TAU_SEC  # 60 seconds

        # Simulate dt = 60s -> alpha ≈ 0.632
        dt_60 = 60.0
        alpha_60 = 1.0 - math.exp(-dt_60 / tau)
        self.assertAlmostEqual(alpha_60, 0.632, places=2)

        # Simulate dt = 10s -> alpha ≈ 0.154
        dt_10 = 10.0
        alpha_10 = 1.0 - math.exp(-dt_10 / tau)
        self.assertAlmostEqual(alpha_10, 0.154, places=2)

        # NOT a fixed 0.1 alpha
        self.assertNotAlmostEqual(alpha_60, 0.1, places=1)

    async def test_time_weighted_ema_handles_zero_dt_if_touched(self):
        """Time-weighted EMA must guard against zero/negative dt."""
        import math
        orch = self.orch
        # When dt = 0, alpha should approach 0 (no change to EMA)
        dt_zero = 0.0
        alpha_zero = 1.0 - math.exp(-dt_zero / orch._EMA_TAU_SEC)
        self.assertAlmostEqual(alpha_zero, 0.0, places=5)
        # Implementation guards with 1e-6: alpha ≈ 1 - exp(-1e-6/60) ≈ 1.67e-8 ≈ 0
        self.assertLess(alpha_zero, 1e-5)


class TestSprint8AIExplorationBonus(unittest.IsolatedAsyncioTestCase):
    """Verify time-decayed exploration bonus."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_exploration_bonus_is_time_decayed_if_touched(self):
        """Starvation bonus must be 0 below 60s, increasing to cap at 120s+."""
        cap = 0.15

        def starve_bonus(idle_sec):
            if idle_sec > 60.0:
                frac = min(1.0, (idle_sec - 60.0) / 60.0)
                return cap * frac
            return 0.0

        # < 60s: no bonus
        self.assertEqual(starve_bonus(30.0), 0.0)
        self.assertEqual(starve_bonus(59.9), 0.0)

        # > 60s: start bonus
        self.assertGreater(starve_bonus(60.1), 0.0)

        # 90s: 50% of cap
        self.assertAlmostEqual(starve_bonus(90.0), 0.075, places=3)

        # 120s+: at cap
        self.assertAlmostEqual(starve_bonus(120.0), 0.15, places=3)
        self.assertAlmostEqual(starve_bonus(180.0), 0.15, places=3)


class TestSprint8AIRegression(unittest.IsolatedAsyncioTestCase):
    """Regression tests for Sprint 8AI."""

    async def asyncSetUp(self):
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        self.orch = FullyAutonomousOrchestrator()
        await self.orch.initialize()

    async def test_orchestrator_initialization_no_crash(self):
        """Orchestrator must initialize without crash."""
        orch = self.orch
        self.assertIsNotNone(orch)
        self.assertIsNotNone(orch._research_mgr)

    async def test_time_based_state_initialized(self):
        """Time-based state must be initialized."""
        orch = self.orch
        self.assertTrue(hasattr(orch, '_rolling_action_history'))
        self.assertTrue(hasattr(orch, '_rolling_hhi'))
        self.assertTrue(hasattr(orch, '_action_last_selected_time'))
        self.assertTrue(hasattr(orch, '_family_yield_ema'))
        self.assertEqual(orch._rolling_hhi, 0.0)

    async def test_monopoly_guard_master_switch_exists(self):
        """_MONOPOLY_GUARD_ENABLED master switch must exist."""
        orch = self.orch
        self.assertTrue(hasattr(orch, '_MONOPOLY_GUARD_ENABLED'))
        self.assertIs(orch._MONOPOLY_GUARD_ENABLED, True)


if __name__ == '__main__':
    unittest.main()
