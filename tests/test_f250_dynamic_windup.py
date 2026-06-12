"""F250 + F278A: Dynamic windup lead — scales with sprint duration.

Sprint evolution:
  F250     : 30% ratio with [30, 180] clamp
  F272A    : 10% ratio with [15, 60] clamp    (over-corrected: starved windup)
  F273B    : 20% ratio with [20, 90] clamp    (over-corrected again)
  F278A    : 30% ratio with [30, 180] clamp    (current: matches F221-ABORT guard)

Rationale for F278A: The F221-ABORT pre-flight guard in core/__main__.py uses
30%/[30, 180] to compute the minimum active window. The scheduler now uses the
same formula so both agree on the active budget. The 30s floor ensures every
sprint retains at least MIN_ACTIVE_WINDOW_S=30s for evidence collection.
"""

import unittest

from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestF278ADynamicWindup(unittest.TestCase):
    """F278A: 30% duration, clamped to [30, 180]. Matches F221-ABORT guard."""

    def test_300s_sprint_windup_90s_active_210s(self):
        """300s sprint -> windup=90s (30% of 300), active=210s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        self.assertEqual(cfg.effective_windup_lead_s, 90)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 210)

    def test_300s_sprint_active_budget_meets_minimum(self):
        """300s sprint active_budget must be >= 210s (30% windup)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        self.assertGreaterEqual(active, 210)

    def test_600s_sprint_windup_at_ceiling(self):
        """600s sprint -> windup=180s (30% = 180, at ceiling)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 180)

    def test_600s_sprint_active_budget_420s(self):
        """600s sprint -> active_budget=420s."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 420)

    def test_60s_sprint_respects_floor(self):
        """60s sprint -> windup=30s (F278A floor, 18s computed, clamped up)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 30)

    def test_60s_sprint_active_budget_30s(self):
        """60s sprint -> active_budget=30s (F221-ABORT MIN_ACTIVE_WINDOW_S)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 30)

    def test_120s_sprint_windup_36s(self):
        """120s sprint -> windup=36s (30% of 120, no clamp needed)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 36)

    def test_1800s_sprint_capped_at_180s(self):
        """1800s sprint -> windup=180s (F278A ceiling)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 180)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 1620)

    def test_windup_lead_property_returns_float(self):
        """effective_windup_lead_s returns float for arithmetic compatibility."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        result = cfg.effective_windup_lead_s
        self.assertIsInstance(result, (int, float))

    def test_original_windup_lead_s_default_unchanged(self):
        """SprintSchedulerConfig.windup_lead_s default remains 180.0 (backward compat)."""
        cfg = SprintSchedulerConfig()
        self.assertEqual(cfg.windup_lead_s, 180.0)

    def test_900s_sprint_windup_at_ceiling(self):
        """900s sprint -> windup=180s (30% = 270, capped at 180)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=900)
        self.assertEqual(cfg.effective_windup_lead_s, 180)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 720)

    def test_f278a_floor_at_30s_never_below(self):
        """F278A floor is 30s (matches F221-ABORT MIN_ACTIVE_WINDOW_S)."""
        for dur in (30, 60, 90, 99):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 30.0,
                f"F278A floor broken at dur={dur}s",
            )

    def test_f278a_ceiling_at_180s_never_above(self):
        """F278A ceiling is 180s. Any duration >= 600s caps at 180s."""
        for dur in (600, 900, 1200, 1800, 3600):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, 180.0,
                f"F278A ceiling broken at dur={dur}s",
            )

    def test_100s_sprint_windup_30s(self):
        """100s sprint -> windup=30s (30% = 30, exactly at floor)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=100)
        self.assertEqual(cfg.effective_windup_lead_s, 30)

    def test_200s_sprint_windup_60s(self):
        """200s sprint -> windup=60s (30% of 200)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=200)
        self.assertEqual(cfg.effective_windup_lead_s, 60)


if __name__ == "__main__":
    unittest.main()
