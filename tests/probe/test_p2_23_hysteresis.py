"""
P2-23: Tests for MemoryPressureHysteresis state machine.

Covers:
- State transition dwell-time enforcement (enter transitions)
- Asymmetric exit hysteresis (slower exits than entries)
- No thrashing when memory oscillates near threshold
- Reset to normal state
"""
from __future__ import annotations

import pytest

from hledac.universal.core.resource_governor import MemoryPressureHysteresis


class TestMemoryPressureHysteresis:
    """P2-23: MemoryPressureHysteresis state machine tests."""

    @pytest.fixture
    def hyst(self) -> MemoryPressureHysteresis:
        """8 GiB total — matches M1 8GB UMA."""
        return MemoryPressureHysteresis(total_gib=8.0)

    # ── Enter transitions ────────────────────────────────────────────────────────

    def test_normal_to_warning_requires_dwell(self, hyst: MemoryPressureHysteresis) -> None:
        """Must stay > 70% (5.6 GiB) for 5s before transitioning normal→warning."""
        now = 100.0
        # First call: crosses threshold, sets _enter_time, stays normal (dwell not met)
        hyst.update(5.7 / 8.0, 5.7, now)
        assert hyst.state == "normal"
        assert hyst._enter_time is not None

        # After 4.9s: still below dwell threshold
        hyst.update(5.7 / 8.0, 5.7, now + 4.9)
        assert hyst.state == "normal"

        # After 5.0s: dwell met → transitions to warning
        hyst.update(5.7 / 8.0, 5.7, now + 5.0)
        assert hyst.state == "warning"

    def test_warning_to_critical_requires_dwell(self, hyst: MemoryPressureHysteresis) -> None:
        """Must stay > 85% (6.8 GiB) for 3s before transitioning warning→critical."""
        now = 100.0
        hyst._state = "warning"
        hyst._enter_time = now

        # Below critical threshold — stay warning
        hyst.update(6.5 / 8.0, 6.5, now + 2.0)
        assert hyst.state == "warning"

        # At threshold for <3s — stay warning
        hyst.update(6.8 / 8.0, 6.8, now + 2.5)
        assert hyst.state == "warning"

        # At threshold for 3s → transition to critical
        hyst.update(6.9 / 8.0, 6.9, now + 3.0)
        assert hyst.state == "critical"

    # ── Exit transitions (asymmetric — slower than enter) ───────────────────────

    def test_critical_exits_only_when_below_floor_for_10s(self, hyst: MemoryPressureHysteresis) -> None:
        """critical→warning requires <75% (6.0 GiB) for 10s."""
        now = 100.0
        hyst._state = "critical"
        hyst._enter_time = now

        # Above exit floor — stay critical
        hyst.update(7.0 / 8.0, 7.0, now + 1.0)
        assert hyst.state == "critical"
        assert hyst._exit_enter_time is None  # Not in exit zone

        # Drop below floor — enter exit zone
        hyst.update(5.9 / 8.0, 5.9, now + 1.1)
        assert hyst.state == "critical"
        assert hyst._exit_enter_time is not None

        # Below floor but not 10s yet — stay critical
        hyst.update(5.9 / 8.0, 5.9, now + 5.0)
        assert hyst.state == "critical"

        # 10s elapsed → exit to warning
        hyst.update(5.9 / 8.0, 5.9, now + 11.1)
        assert hyst.state == "warning"

    def test_warning_exits_only_when_below_floor_for_15s(self, hyst: MemoryPressureHysteresis) -> None:
        """warning→normal requires <60% (4.8 GiB) for 15s."""
        now = 100.0
        hyst._state = "warning"
        hyst._enter_time = now

        # Drop below floor — enter exit zone at t=1.0
        hyst.update(4.7 / 8.0, 4.7, now + 1.0)
        assert hyst._exit_enter_time is not None

        # After 14s in exit zone — still not enough
        hyst.update(4.7 / 8.0, 4.7, now + 15.0)
        assert hyst.state == "warning"

        # After 15s — exit to normal
        hyst.update(4.7 / 8.0, 4.7, now + 16.0)
        assert hyst.state == "normal"

    # ── Thrashing prevention ────────────────────────────────────────────────────

    def test_no_flapping_at_threshold_boundary(self, hyst: MemoryPressureHysteresis) -> None:
        """
        Oscillating around 70% threshold should not cause rapid state changes.

        normal_to_warning dwell = 5s. Rapid crossings of the threshold
        should keep state = normal until dwell is truly met.
        """
        now = 100.0
        hyst.update(6.0 / 8.0, 6.0, now)
        assert hyst.state == "normal"

        # Simulate oscillation: never enough continuous dwell time to transition
        for _ in range(3):
            for delta in [0.1, 0.2, 0.3, 0.4]:
                now += delta
                gib = 5.61 if (int(now * 10) % 2) < 1 else 5.59
                hyst.update(gib / 8.0, gib, now)
            assert hyst.state == "normal"

    def test_critical_to_warning_resets_exit_timer_if_rises_above_floor(
        self, hyst: MemoryPressureHysteresis
    ) -> None:
        """If memory rises back above exit floor during exit dwell, timer resets."""
        now = 100.0
        hyst._state = "critical"
        hyst._enter_time = now

        # Drop below floor — enter exit zone at t=1.0
        hyst.update(5.9 / 8.0, 5.9, now + 1.0)
        assert hyst._exit_enter_time is not None
        exit_start = hyst._exit_enter_time

        # Memory rises above floor — exit timer should reset
        hyst.update(7.5 / 8.0, 7.5, now + 5.0)
        assert hyst.state == "critical"
        assert hyst._exit_enter_time is None  # Reset

        # Drop below again — new exit timer started later
        hyst.update(5.9 / 8.0, 5.9, now + 5.1)
        assert hyst._exit_enter_time is not None
        assert hyst._exit_enter_time > exit_start  # New timer started later

    # ── State queries ───────────────────────────────────────────────────────────

    def test_state_property(self, hyst: MemoryPressureHysteresis) -> None:
        """State property returns current state."""
        assert hyst.state == "normal"
        hyst._state = "warning"
        assert hyst.state == "warning"
        hyst._state = "critical"
        assert hyst.state == "critical"

    # ── Reset ───────────────────────────────────────────────────────────────────

    def test_reset_returns_to_normal(self, hyst: MemoryPressureHysteresis) -> None:
        """Reset clears all state including timers."""
        hyst._state = "critical"
        hyst._enter_time = 50.0
        hyst._exit_enter_time = 75.0

        hyst.reset()

        assert hyst.state == "normal"
        assert hyst._enter_time is None
        assert hyst._exit_enter_time is None

    # ── Total GiB affects thresholds ────────────────────────────────────────────

    def test_thresholds_scale_with_total_gib(self) -> None:
        """16 GiB system should have proportionally higher GiB thresholds."""
        hyst_8 = MemoryPressureHysteresis(total_gib=8.0)
        hyst_16 = MemoryPressureHysteresis(total_gib=16.0)
        now = 100.0

        # 70% of 8 GiB = 5.6 GiB → still normal
        hyst_8.update(5.6 / 8.0, 5.6, now)
        assert hyst_8.state == "normal"

        # 70% of 16 GiB = 11.2 GiB, after 5s dwell → warning
        for _ in range(21):
            hyst_16.update(11.2 / 16.0, 11.2, now)
            now += 0.25
        assert hyst_16.state == "warning"

    # ── Instant transitions (edge cases) ────────────────────────────────────────

    def test_already_at_critical_threshold_in_warning_state(self) -> None:
        """If already above critical threshold when entering warning, short dwell."""
        hyst = MemoryPressureHysteresis(total_gib=8.0)
        now = 100.0
        hyst._state = "warning"
        hyst._enter_time = now

        # Immediately above critical threshold — need 3s dwell
        hyst.update(6.8 / 8.0, 6.8, now + 1.0)
        assert hyst.state == "warning"

        hyst.update(6.8 / 8.0, 6.8, now + 3.0)
        assert hyst.state == "critical"


class TestMemoryPressureHysteresisConstants:
    """Verify threshold constants match the documented state diagram."""

    def test_normal_to_warning_threshold(self) -> None:
        """normal_to_warning: 70% of total GiB for 5s."""
        hyst = MemoryPressureHysteresis(total_gib=8.0)
        ratio, dwell = hyst.THRESHOLDS["normal_to_warning"]
        assert ratio == 0.70
        assert dwell == 5.0

    def test_warning_to_critical_threshold(self) -> None:
        """warning_to_critical: 85% of total GiB for 3s."""
        hyst = MemoryPressureHysteresis(total_gib=8.0)
        ratio, dwell = hyst.THRESHOLDS["warning_to_critical"]
        assert ratio == 0.85
        assert dwell == 3.0

    def test_exit_floors(self) -> None:
        """Exit floors are asymmetric (slower exits)."""
        hyst = MemoryPressureHysteresis(total_gib=8.0)
        assert hyst.EXIT_FLOOR_WARNING == 0.60
        assert hyst.EXIT_FLOOR_CRITICAL == 0.75
        assert hyst.EXIT_DWELL_WARNING == 15.0
        assert hyst.EXIT_DWELL_CRITICAL == 10.0
