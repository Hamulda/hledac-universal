"""F250: Dynamic windup lead — scales with sprint duration to prevent short-sprint starvation."""

import unittest

from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestF250DynamicWindup(unittest.TestCase):
    """Verify dynamic windup scaling preserves 600s+ behavior while fixing short sprints."""

    def test_300s_sprint_windup_90s_active_210s(self):
        """300s sprint → windup=90s, active_budget=210s (30% ratio capped at 90s)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        self.assertEqual(cfg.effective_windup_lead_s, 90)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 210)

    def test_300s_sprint_active_budget_meets_minimum(self):
        """300s sprint active_budget must be >= 210s to allow cold target execution."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        self.assertGreaterEqual(active, 210)

    def test_600s_sprint_preserves_original_behavior(self):
        """600s sprint → windup=180s (preserves original fixed value)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 180)

    def test_600s_sprint_active_budget_unchanged(self):
        """600s sprint → active_budget=420s (original behavior preserved)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 420)

    def test_60s_sprint_respects_minimum_windup(self):
        """60s sprint → windup=30s (minimum floor, not 18s from 30% ratio)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 30)

    def test_60s_sprint_active_budget_30s(self):
        """60s sprint → active_budget=30s (minimum viable window)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 30)

    def test_120s_sprint_windup_at_ratio(self):
        """120s sprint → windup=36s (30% of 120, between min=30 and max=180)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 36)

    def test_1800s_sprint_capped_at_max(self):
        """1800s sprint → windup=180s (hard cap, not 540s from 30% ratio)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 180)

    def test_windup_lead_property_returns_float(self):
        """effective_windup_lead_s returns float for arithmetic compatibility."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        result = cfg.effective_windup_lead_s
        self.assertIsInstance(result, (int, float))

    def test_original_windup_lead_s_default_unchanged(self):
        """SprintSchedulerConfig.windup_lead_s default remains 180.0 (backward compat)."""
        cfg = SprintSchedulerConfig()
        self.assertEqual(cfg.windup_lead_s, 180.0)

    def test_900s_sprint_windup_180s_capped(self):
        """900s sprint → windup=180s (30% would be 270, capped at 180)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=900)
        self.assertEqual(cfg.effective_windup_lead_s, 180)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 720)


if __name__ == "__main__":
    unittest.main()