"""
Sprint 7F — UMA Watchdog Tests
===============================

Tests for UmaWatchdog (utils/uma_budget.py):
1. watchdog helper exists
2. watchdog interval default = 0.5s
3. watchdog uses thresholds from uma_budget.py
4. watchdog is fail-open
5. watchdog has debounce / cooldown
6. watchdog can start/stop without leaks
7. new background task is NOT bare fire-and-forget (tracked pattern)
8. watchdog registration seam exists
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestUmaWatchdogHelpers(unittest.TestCase):
    """Test class existence and basic invariants."""

    def test_uma_watchdog_class_exists(self):
        """1. watchdog helper exists."""
        from hledac.universal.utils.uma_budget import UmaWatchdog, UmaWatchdogCallbacks
        self.assertTrue(callable(UmaWatchdog))
        self.assertTrue(callable(UmaWatchdogCallbacks))

    def test_watchdog_interval_default_0_5s(self):
        """2. watchdog interval default = 0.5s."""
        from hledac.universal.utils.uma_budget import UmaWatchdog
        w = UmaWatchdog()
        self.assertEqual(w.interval, 0.5)

    def test_watchdog_uses_thresholds_from_uma_budget(self):
        """3. watchdog uses threshold values from uma_budget module (dynamically computed)."""
        from hledac.universal.utils import uma_budget
        self.assertTrue(hasattr(uma_budget, "_WARN_THRESHOLD_MB"))
        self.assertTrue(hasattr(uma_budget, "_CRITICAL_THRESHOLD_MB"))
        self.assertTrue(hasattr(uma_budget, "_EMERGENCY_THRESHOLD_MB"))
        # Verify thresholds are derived from _UMA_TOTAL_MB with correct ratios
        self.assertAlmostEqual(uma_budget._WARN_THRESHOLD_MB / uma_budget._UMA_TOTAL_MB, 0.87, places=2)
        self.assertAlmostEqual(uma_budget._CRITICAL_THRESHOLD_MB / uma_budget._UMA_TOTAL_MB, 0.93, places=2)
        self.assertAlmostEqual(uma_budget._EMERGENCY_THRESHOLD_MB / uma_budget._UMA_TOTAL_MB, 0.97, places=2)


class TestWatchdogFailOpen(unittest.TestCase):
    """Test fail-open behavior."""

    def test_watchdog_is_fail_open_on_poll_error(self):
        """4. watchdog is fail-open (treats exceptions as normal)."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        cb = MagicMock()
        w = UmaWatchdog(callbacks=cb, interval=0.05)

        async def run_watchdog():
            task = w.start()
            try:
                await asyncio.wait_for(task, timeout=0.25)
            except asyncio.TimeoutError:
                pass  # expected — we timeout while watchdog is still running
            w.stop()

        with patch("hledac.universal.utils.uma_budget.get_uma_pressure_level", side_effect=RuntimeError("sensor error")):
            asyncio.run(run_watchdog())

        cb.on_warn.assert_not_called()
        cb.on_critical.assert_not_called()
        cb.on_emergency.assert_not_called()


class TestWatchdogDebounce(unittest.TestCase):
    """Test debounce / cooldown behavior."""

    def test_watchdog_debounce_same_level_not_fired_twice(self):
        """5a. watchdog debounce — same level not re-triggered within cooldown."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        cb = MagicMock()
        w = UmaWatchdog(callbacks=cb, interval=0.05)
        w.DEBOUNCE_SECONDS = 0.3

        async def run():
            task = w.start()
            try:
                await asyncio.wait_for(task, timeout=0.25)
            except asyncio.TimeoutError:
                pass
            w.stop()

        with patch("hledac.universal.utils.uma_budget.get_uma_pressure_level", return_value=(80, "warn")):
            with patch("hledac.universal.utils.uma_budget.get_uma_snapshot", return_value={"uma_used_mb": 6500, "uma_usage_pct": 80}):
                asyncio.run(run())

        self.assertEqual(cb.on_warn.call_count, 1)

    def test_watchdog_debounce_transition_to_higher_fires(self):
        """5b. watchdog debounce — transition to higher level fires even within debounce window."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        cb = MagicMock()
        w = UmaWatchdog(callbacks=cb, interval=0.05)
        w.DEBOUNCE_SECONDS = 0.3

        call_count = [0]
        def mock_level():
            call_count[0] += 1
            if call_count[0] == 1:
                return (80, "warn")
            return (85, "critical")

        async def run():
            task = w.start()
            try:
                await asyncio.wait_for(task, timeout=0.35)
            except asyncio.TimeoutError:
                pass
            w.stop()

        with patch("hledac.universal.utils.uma_budget.get_uma_pressure_level", side_effect=mock_level):
            with patch("hledac.universal.utils.uma_budget.get_uma_snapshot", return_value={"uma_used_mb": 6800, "uma_usage_pct": 85}):
                asyncio.run(run())

        self.assertGreaterEqual(cb.on_warn.call_count, 1)
        self.assertGreaterEqual(cb.on_critical.call_count, 1)


class TestWatchdogLifecycle(unittest.TestCase):
    """Test start/stop/leak-free lifecycle."""

    def test_watchdog_start_stop_no_leak(self):
        """6. watchdog can start/stop without leaks."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        w = UmaWatchdog(interval=0.05)

        async def run():
            task = w.start()
            self.assertTrue(w.is_running)
            self.assertIsNotNone(task)
            w.stop()
            self.assertFalse(w.is_running)

        asyncio.run(run())

    def test_watchdog_double_start_raises(self):
        """6b. starting twice raises RuntimeError."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        w = UmaWatchdog(interval=0.05)

        async def run():
            w.start()
            with self.assertRaises(RuntimeError):
                w.start()
            w.stop()

        asyncio.run(run())

    def test_watchdog_task_is_trackable(self):
        """7. new background task is NOT bare fire-and-forget — returns trackable Task."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        w = UmaWatchdog(interval=0.5)

        async def run():
            task = w.start()
            self.assertIsInstance(task, asyncio.Task)
            self.assertEqual(task.get_name(), "uma_watchdog")
            w.stop()

        asyncio.run(run())


class TestWatchdogRegistrationSeam(unittest.TestCase):
    """Test that a lightweight registration seam exists without new orchestrator."""

    def test_watchdog_callbacks_class_exists(self):
        """8. UmaWatchdogCallbacks registration seam exists."""
        from hledac.universal.utils.uma_budget import UmaWatchdogCallbacks
        cb = UmaWatchdogCallbacks()
        self.assertTrue(callable(cb.on_warn))
        self.assertTrue(callable(cb.on_critical))
        self.assertTrue(callable(cb.on_emergency))

    def test_watchdog_callback_integration(self):
        """8b. callback integration — on_emergency called at emergency level."""
        from hledac.universal.utils.uma_budget import UmaWatchdog

        cb = MagicMock()
        w = UmaWatchdog(callbacks=cb, interval=0.05)

        async def run():
            task = w.start()
            try:
                await asyncio.wait_for(task, timeout=0.25)
            except asyncio.TimeoutError:
                pass
            w.stop()

        with patch("hledac.universal.utils.uma_budget.get_uma_pressure_level", return_value=(90, "emergency")):
            with patch("hledac.universal.utils.uma_budget.get_uma_snapshot", return_value={"uma_used_mb": 7500, "uma_usage_pct": 90}):
                asyncio.run(run())

        cb.on_emergency.assert_called_once_with({"uma_used_mb": 7500, "uma_usage_pct": 90})


if __name__ == "__main__":
    unittest.main(verbosity=2)
