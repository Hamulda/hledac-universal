"""F250: Dynamic windup lead — scales with sprint duration to prevent short-sprint starvation.

Sprint evolution:
  F250     : 30% ratio with [30, 180] clamp   (over-spend on short sprints)
  F272A    : 10% ratio with [15, 60] clamp    (over-corrected: starved windup)
  F273B    : 20% ratio with [20, 90] clamp    (current contract)

Rationale for F273B: 20% gives windup enough budget for the pattern
extraction drain (F273C) + Hermes synthesis + DuckDB ingest, while still
preserving a usable active window for 60s+ sprints. The 20s floor gives
short sprints 33% of budget in windup (vs F272A's 25%); the 90s ceiling
caps long thorough runs at 5% overhead (vs F250's 10%).

The F250 backward-compat tests below are updated to assert the F273B
contract; the property is still "scales with duration" but with the
M1-8GB-friendly envelope. See tests/test_sprint_f273.py for the full
F273B test suite including the cycle-adaptive component.
"""

import unittest

from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestF250DynamicWindup(unittest.TestCase):
    """F273B amendment: 20% duration, clamped to [20, 90]."""

    def test_300s_sprint_windup_60s_active_240s(self):
        """300s sprint -> windup=60s (20% of 300), active=240s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        self.assertEqual(cfg.effective_windup_lead_s, 60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 240)

    def test_300s_sprint_active_budget_meets_minimum(self):
        """300s sprint active_budget must be >= 240s (F273B)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        self.assertGreaterEqual(active, 240)

    def test_600s_sprint_windup_at_ceiling(self):
        """600s sprint -> windup=90s (20% = 120, clamped to ceiling)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 90)

    def test_600s_sprint_active_budget_510s(self):
        """600s sprint -> active_budget=510s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 510)

    def test_60s_sprint_respects_floor(self):
        """60s sprint -> windup=20s (F273B floor, 12s computed, clamped up)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 20)

    def test_60s_sprint_active_budget_40s(self):
        """60s sprint -> active_budget=40s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 40)

    def test_120s_sprint_windup_24s(self):
        """120s sprint -> windup=24s (20% of 120, no clamp)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 24)

    def test_1800s_sprint_capped_at_90s(self):
        """1800s sprint -> windup=90s (F273B: 270s LESS windup than F250)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 90)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 1710)

    def test_windup_lead_property_returns_float(self):
        """effective_windup_lead_s returns float for arithmetic compatibility."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        result = cfg.effective_windup_lead_s
        self.assertIsInstance(result, (int, float))

    def test_original_windup_lead_s_default_unchanged(self):
        """SprintSchedulerConfig.windup_lead_s default remains 180.0 (backward compat)."""
        cfg = SprintSchedulerConfig()
        self.assertEqual(cfg.windup_lead_s, 180.0)

    def test_900s_sprint_windup_capped(self):
        """900s sprint -> windup=90s (20% = 180, capped at 90)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=900)
        self.assertEqual(cfg.effective_windup_lead_s, 90)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 810)

    def test_f273b_floor_at_20s_never_below(self):
        """F273B floor is 20s. Sub-100s sprints all hit the floor."""
        for dur in (30, 60, 90, 99):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 20.0,
                f"F273B floor broken at dur={dur}s",
            )

    def test_f273b_ceiling_at_90s_never_above(self):
        """F273B ceiling is 90s. Any duration >= 450s caps at 90s."""
        for dur in (450, 600, 900, 1200, 1800, 3600):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 90.0,
                f"F273B ceiling broken at dur={dur}s",
            )


if __name__ == "__main__":
    unittest.main()
