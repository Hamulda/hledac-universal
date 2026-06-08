"""F250: Dynamic windup lead — scales with sprint duration to prevent short-sprint starvation.

Sprint F272A amendment: 30% ratio with [30, 180] clamp → 10% ratio with [15, 60] clamp.
Rationale: 60-120s sprints spent 25-50% of their budget in windup under F250.
The F272A floor of 15s preserves a 45s+ active window for any quick sprint,
while the 60s ceiling still bounds long-sprint windup overhead.

Original F250 was correct for 300-1800s thorough runs but starved short
sprints. F272A amends the contract — the F250 backward-compat tests below
are updated to assert the new contract; the property is still "scales with
duration" but with a tighter, more M1-8GB-friendly envelope.
"""

import unittest

from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestF250DynamicWindup(unittest.TestCase):
    """F272A amendment: 10% duration, clamped to [15, 60]."""

    def test_300s_sprint_windup_30s_active_270s(self):
        """300s sprint -> windup=30s (10% of 300), active=270s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        self.assertEqual(cfg.effective_windup_lead_s, 30)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 270)

    def test_300s_sprint_active_budget_meets_minimum(self):
        """300s sprint active_budget must be >= 270s (F272A gives MORE active than F250)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        self.assertGreaterEqual(active, 270)

    def test_600s_sprint_preserves_envelope(self):
        """600s sprint -> windup=60s (10% of 600, exactly at the cap boundary)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 60)

    def test_600s_sprint_active_budget_540s(self):
        """600s sprint -> active_budget=540s (F272A: 90s MORE active than F250)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 540)

    def test_60s_sprint_respects_minimum_floor(self):
        """60s sprint -> windup=15s (F272A floor, not 30s from F250 which broke 50% of budget)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 15)

    def test_60s_sprint_active_budget_45s(self):
        """60s sprint -> active_budget=45s (F272A: 15s MORE active than F250)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 45)

    def test_120s_sprint_windup_at_floor(self):
        """120s sprint -> windup=15s (12s computed but clamped up to floor)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 15)

    def test_1800s_sprint_capped_at_60s(self):
        """1800s sprint -> windup=60s (F272A: 120s LESS windup than F250, active 1800-60=1740s)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 1740)

    def test_windup_lead_property_returns_float(self):
        """effective_windup_lead_s returns float for arithmetic compatibility."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        result = cfg.effective_windup_lead_s
        self.assertIsInstance(result, (int, float))

    def test_original_windup_lead_s_default_unchanged(self):
        """SprintSchedulerConfig.windup_lead_s default remains 180.0 (backward compat)."""
        cfg = SprintSchedulerConfig()
        self.assertEqual(cfg.windup_lead_s, 180.0)

    def test_900s_sprint_windup_60s_capped(self):
        """900s sprint -> windup=60s (10% = 90, capped at 60)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=900)
        self.assertEqual(cfg.effective_windup_lead_s, 60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 840)

    def test_f272a_floor_at_15s_never_below(self):
        """F272A floor is 15s. Sub-150s sprints all hit the floor."""
        for dur in (30, 60, 90, 120, 149):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 15.0,
                f"F272A floor broken at dur={dur}s",
            )

    def test_f272a_ceiling_at_60s_never_above(self):
        """F272A ceiling is 60s. Any duration >= 600s caps at 60s."""
        for dur in (600, 900, 1200, 1800, 3600):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 60.0,
                f"F272A ceiling broken at dur={dur}s",
            )


if __name__ == "__main__":
    unittest.main()
