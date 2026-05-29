"""F253: Sprint tier system — defines minimum viable sprint lengths and Hermes budget per tier."""

import unittest

from hledac.universal.runtime.sprint_scheduler import (
    SPRINT_TIERS,
    SprintSchedulerConfig,
    SprintTooShortError,
    detect_sprint_tier,
)


class TestF253SprintTiers(unittest.TestCase):
    """Verify sprint tier detection and SPRINT_TIERS definitions."""

    def test_quick_tier_duration_range_60_to_179(self):
        """60-179s sprint → tier='quick'."""
        self.assertEqual(detect_sprint_tier(60), "quick")
        self.assertEqual(detect_sprint_tier(120), "quick")
        self.assertEqual(detect_sprint_tier(179), "quick")

    def test_standard_tier_duration_range_180_to_299(self):
        """180-299s sprint → tier='standard'."""
        self.assertEqual(detect_sprint_tier(180), "standard")
        self.assertEqual(detect_sprint_tier(200), "standard")
        self.assertEqual(detect_sprint_tier(299), "standard")

    def test_deep_tier_duration_range_300_to_599(self):
        """300-599s sprint → tier='deep'."""
        self.assertEqual(detect_sprint_tier(300), "deep")
        self.assertEqual(detect_sprint_tier(400), "deep")
        self.assertEqual(detect_sprint_tier(599), "deep")

    def test_thorough_tier_duration_600_plus(self):
        """600s+ sprint → tier='thorough'."""
        self.assertEqual(detect_sprint_tier(600), "thorough")
        self.assertEqual(detect_sprint_tier(900), "thorough")
        self.assertEqual(detect_sprint_tier(1800), "thorough")

    def test_detect_sprint_tier_raises_for_59s(self):
        """Duration < 60s raises SprintTooShortError."""
        with self.assertRaises(SprintTooShortError) as ctx:
            detect_sprint_tier(59)
        self.assertIn("60s", str(ctx.exception))

    def test_detect_sprint_tier_raises_for_0s(self):
        """Duration = 0 raises SprintTooShortError."""
        with self.assertRaises(SprintTooShortError):
            detect_sprint_tier(0)

    def test_detect_sprint_tier_raises_for_negative(self):
        """Negative duration raises SprintTooShortError."""
        with self.assertRaises(SprintTooShortError):
            detect_sprint_tier(-10)

    def test_sprint_tiers_dict_has_four_tiers(self):
        """SPRINT_TIERS contains quick, standard, deep, thorough."""
        self.assertEqual(set(SPRINT_TIERS.keys()), {"quick", "standard", "deep", "thorough"})

    def test_quick_tier_hermes_disabled(self):
        """quick tier has hermes=False."""
        self.assertFalse(SPRINT_TIERS["quick"]["hermes"])

    def test_standard_tier_hermes_enabled(self):
        """standard tier has hermes=True."""
        self.assertTrue(SPRINT_TIERS["standard"]["hermes"])

    def test_deep_tier_hermes_enabled(self):
        """deep tier has hermes=True."""
        self.assertTrue(SPRINT_TIERS["deep"]["hermes"])

    def test_thorough_tier_hermes_enabled(self):
        """thorough tier has hermes=True."""
        self.assertTrue(SPRINT_TIERS["thorough"]["hermes"])

    def test_quick_tier_min_duration_60(self):
        """quick tier minimum duration is 60s."""
        self.assertEqual(SPRINT_TIERS["quick"]["min_duration"], 60)

    def test_standard_tier_min_duration_180(self):
        """standard tier minimum duration is 180s."""
        self.assertEqual(SPRINT_TIERS["standard"]["min_duration"], 180)

    def test_deep_tier_min_duration_300(self):
        """deep tier minimum duration is 300s."""
        self.assertEqual(SPRINT_TIERS["deep"]["min_duration"], 300)

    def test_thorough_tier_min_duration_600(self):
        """thorough tier minimum duration is 600s."""
        self.assertEqual(SPRINT_TIERS["thorough"]["min_duration"], 600)


class TestF253HermesBudget(unittest.TestCase):
    """Verify adaptive Hermes synthesis budget: 35% of active window, min 30s."""

    def test_hermes_budget_quick_sprint_30s_floor(self):
        """60s quick sprint (windup=30s, active=30s) → hermes_budget=30s (floor)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60)
        self.assertEqual(cfg.hermes_budget_s, 30)

    def test_hermes_budget_300s_sprint(self):
        """300s sprint (windup=90s, active=210s) → hermes_budget=73s (35%, floor=30s)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        expected = max(30, int(active * 0.35))
        self.assertEqual(cfg.hermes_budget_s, expected)
        self.assertEqual(cfg.hermes_budget_s, 73)

    def test_hermes_budget_600s_sprint(self):
        """600s sprint (windup=180s, active=420s) → hermes_budget=147s (35%)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        expected = max(30, int(active * 0.35))
        self.assertEqual(cfg.hermes_budget_s, expected)
        self.assertEqual(cfg.hermes_budget_s, 147)

    def test_hermes_budget_900s_sprint(self):
        """900s sprint (windup=180s, active=720s) → hermes_budget=252s (35%, cap=180 on windup)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=900)
        active = cfg.sprint_duration_s - cfg.effective_windup_lead_s
        expected = max(30, int(active * 0.35))
        self.assertEqual(cfg.hermes_budget_s, expected)

    def test_hermes_budget_returns_float(self):
        """hermes_budget_s returns float/int for arithmetic compatibility."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300)
        result = cfg.hermes_budget_s
        self.assertIsInstance(result, (int, float))

    def test_hermes_budget_above_floor_for_large_sprint(self):
        """Large sprint active window produces hermes_budget well above 30s floor."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1200)
        self.assertGreater(cfg.hermes_budget_s, 30)


class TestF253BackwardCompatibility(unittest.TestCase):
    """Verify 600s sprint (old default) behaves identically to pre-F253 behavior."""

    def test_600s_sprint_still_thorough_tier(self):
        """600s sprint → thorough tier (backward compat)."""
        self.assertEqual(detect_sprint_tier(600), "thorough")

    def test_600s_sprint_windup_unchanged(self):
        """600s sprint → windup=180s (original fixed value, pre-F253)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 180)

    def test_600s_sprint_active_budget_unchanged(self):
        """600s sprint → active=420s (original behavior preserved)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600)
        self.assertEqual(cfg.sprint_duration_s - cfg.effective_windup_lead_s, 420)

    def test_600s_sprint_default_config_unchanged(self):
        """SprintSchedulerConfig() defaults are unchanged."""
        cfg = SprintSchedulerConfig()
        self.assertEqual(cfg.sprint_duration_s, 1800.0)
        self.assertEqual(cfg.windup_lead_s, 180.0)


if __name__ == "__main__":
    unittest.main()
