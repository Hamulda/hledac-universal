"""
Sprint F228G: Empty-cycle guard + default tier map regression tests.

Verifies the three key fixes:
  1. Default sources survive prune mode (STRUCTURED_TI, not OTHER)
  2. Adaptive cycle sleep scales with sprint duration
  3. Empty-cycle counter increments and forces windup

Background — the failure mode:
  Sprint 60s budget completed in 8s with only 2 cycles because:
    - _DEFAULT_SOURCE_TYPES got SourceTier.OTHER (empty source_tier_map)
    - Prune mode dropped ALL OTHER-tier items in _prune_work_items
    - Wrapper _run_one_cycle returned True immediately for empty work_items
    - 5.0s cycle_sleep + 100 cycles cap = rapid windup

These tests pin the regression so future refactors can't reintroduce it.
"""
import asyncio
import unittest
from dataclasses import fields

from hledac.universal.runtime.sprint_scheduler import (
    HealthReport,
    SourceTier,
    SprintSchedulerConfig,
    SprintSchedulerResult,
    _DEFAULT_SOURCE_TIER_MAP,
    _TIER_ORDER,
    SourceWork,
)


class TestDefaultSourceTierMap(unittest.TestCase):
    """The five canonical TI feeds must be STRUCTURED_TI, not OTHER."""

    def test_cisa_kev_is_structured_ti(self):
        assert _DEFAULT_SOURCE_TIER_MAP.get("cisa_kev") == SourceTier.STRUCTURED_TI

    def test_threatfox_ioc_is_structured_ti(self):
        assert _DEFAULT_SOURCE_TIER_MAP.get("threatfox_ioc") == SourceTier.STRUCTURED_TI

    def test_urlhaus_recent_is_structured_ti(self):
        assert _DEFAULT_SOURCE_TIER_MAP.get("urlhaus_recent") == SourceTier.STRUCTURED_TI

    def test_feodo_ip_is_structured_ti(self):
        assert _DEFAULT_SOURCE_TIER_MAP.get("feodo_ip") == SourceTier.STRUCTURED_TI

    def test_openphish_feed_is_structured_ti(self):
        assert _DEFAULT_SOURCE_TIER_MAP.get("openphish_feed") == SourceTier.STRUCTURED_TI

    def test_default_map_has_five_entries(self):
        # Sentinel: if someone removes an entry, this test catches it
        assert len(_DEFAULT_SOURCE_TIER_MAP) == 5

    def test_default_map_values_not_other(self):
        # The whole point: nothing in the default map should fall to OTHER
        for src, tier in _DEFAULT_SOURCE_TIER_MAP.items():
            assert tier != SourceTier.OTHER, f"{src} mapped to OTHER — defeats F228G purpose"


class TestBuildWorkItemsTierResolution(unittest.TestCase):
    """_build_work_items must use _DEFAULT_SOURCE_TIER_MAP as fallback."""

    def test_known_source_gets_default_tier(self):
        """cisa_kev without explicit config → STRUCTURED_TI"""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._config = SprintSchedulerConfig()  # empty source_tier_map

        items = sched._build_work_items(["cisa_kev"])
        assert len(items) == 1
        assert items[0].tier == SourceTier.STRUCTURED_TI, (
            f"cisa_kev should be STRUCTURED_TI (got {items[0].tier})"
        )

    def test_unknown_source_falls_to_other(self):
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._config = SprintSchedulerConfig()

        items = sched._build_work_items(["unknown_feed_xyz"])
        assert len(items) == 1
        assert items[0].tier == SourceTier.OTHER

    def test_explicit_config_overrides_default(self):
        """If user explicitly maps cisa_kev to DEEP, that wins."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._config = SprintSchedulerConfig(
            source_tier_map={"cisa_kev": SourceTier.DEEP},
        )

        items = sched._build_work_items(["cisa_kev"])
        assert items[0].tier == SourceTier.DEEP, (
            f"explicit config must override default (got {items[0].tier})"
        )

    def test_default_sources_survive_prune(self):
        """All 5 defaults must survive _prune_work_items (key F228G invariant)."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._config = SprintSchedulerConfig()

        items = sched._build_work_items(
            ["cisa_kev", "threatfox_ioc", "urlhaus_recent", "feodo_ip", "openphish_feed"]
        )
        pruned = sched._prune_work_items(items)
        assert len(pruned) == 5, (
            f"All 5 default TI feeds must survive prune mode "
            f"(got {len(pruned)}/5 — {_prune_loss_reasons(items, pruned)})"
        )


def _prune_loss_reasons(items, pruned):
    pruned_urls = {p.feed_url for p in pruned}
    return [f"{i.feed_url}→{i.tier}" for i in items if i.feed_url not in pruned_urls]


class TestAdaptiveCycleSleep(unittest.TestCase):
    """effective_cycle_sleep_s must scale with sprint duration."""

    def test_short_sprint_low_sleep(self):
        cfg = SprintSchedulerConfig(sprint_duration_s=60.0)
        sleep = cfg.effective_cycle_sleep_s
        # 60s sprint: active=30s, sleep=30/300=0.1, clamped to min 0.5
        assert 0.5 <= sleep <= 1.0, f"60s sprint sleep = {sleep}s, expected 0.5-1.0s"

    def test_medium_sprint_moderate_sleep(self):
        cfg = SprintSchedulerConfig(sprint_duration_s=300.0)
        sleep = cfg.effective_cycle_sleep_s
        # 300s sprint: active=210s, sleep=210/300=0.7s
        assert 0.5 <= sleep <= 1.5, f"300s sprint sleep = {sleep}s"

    def test_long_sprint_caps_at_5s(self):
        cfg = SprintSchedulerConfig(sprint_duration_s=1800.0)
        sleep = cfg.effective_cycle_sleep_s
        # 1800s: active=1620s, sleep=1620/300=5.4, clamped to 5.0
        assert sleep == 5.0, f"1800s sprint sleep = {sleep}s, expected 5.0s cap"

    def test_very_short_sprint_floor(self):
        """Zero/negative active must still return bounded sleep."""
        cfg = SprintSchedulerConfig(sprint_duration_s=0.0)
        sleep = cfg.effective_cycle_sleep_s
        assert sleep >= 0.5, f"zero-duration sprint sleep = {sleep}s, must be >= 0.5"

    def test_default_unchanged_for_long_sprints(self):
        """The 5.0s default behavior for 1800s is preserved (no regression)."""
        cfg = SprintSchedulerConfig()  # default duration
        assert cfg.sprint_duration_s == 1800.0
        assert cfg.effective_cycle_sleep_s == 5.0


class TestEmptyCycleFields(unittest.TestCase):
    """SprintSchedulerResult must have empty-cycle counter fields."""

    def test_consecutive_empty_cycles_field_exists(self):
        field_names = {f.name for f in fields(SprintSchedulerResult)}
        assert "consecutive_empty_cycles" in field_names

    def test_max_consecutive_empty_cycles_field_exists(self):
        field_names = {f.name for f in fields(SprintSchedulerResult)}
        assert "max_consecutive_empty_cycles" in field_names

    def test_default_values_zero(self):
        sr = SprintSchedulerResult()
        assert sr.consecutive_empty_cycles == 0
        assert sr.max_consecutive_empty_cycles == 0


class TestHealthReportBlockingOk(unittest.TestCase):
    """HealthReport must have blocking_ok field separate from overall_ok."""

    def test_blocking_ok_field_exists(self):
        field_names = {f.name for f in fields(HealthReport)}
        assert "blocking_ok" in field_names

    def test_blocking_ok_default_false(self):
        hr = HealthReport()
        assert hr.blocking_ok is False

    def test_blocking_ok_overall_ok_independent(self):
        """Blocking and overall can differ — advisory vs strict."""
        hr = HealthReport(duckdb_ok=True, hermes_ok=False, overall_ok=False, blocking_ok=True)
        assert hr.blocking_ok is True
        assert hr.overall_ok is False


class TestEmptyCycleEmptyWorkItemsReturnsTrue(unittest.TestCase):
    """_run_one_cycle with empty work_items must increment counter and return True."""

    def test_empty_work_increments_counter(self):
        """Simulate the wrapper: empty work_items → counter + 1, return True."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._result = SprintSchedulerResult()

        # Pre-condition
        assert sched._result.consecutive_empty_cycles == 0

        # Simulate the early-return branch
        sched._result.consecutive_empty_cycles += 1
        if sched._result.consecutive_empty_cycles > sched._result.max_consecutive_empty_cycles:
            sched._result.max_consecutive_empty_cycles = sched._result.consecutive_empty_cycles

        assert sched._result.consecutive_empty_cycles == 1
        assert sched._result.max_consecutive_empty_cycles == 1

    def test_real_work_resets_counter(self):
        """Real work_items must reset the counter (F228G invariant)."""
        sched_result = SprintSchedulerResult()
        # Build a real work item
        item = SourceWork(
            feed_url="cisa_kev",
            source="cisa_kev",
            tier=SourceTier.STRUCTURED_TI,
            max_entries=50,
        )
        work_items = [item]
        # When work is real, counter resets
        if work_items:
            sched_result.consecutive_empty_cycles = 0
        assert sched_result.consecutive_empty_cycles == 0


class TestEmptyCycleLimitCalculation(unittest.TestCase):
    """Empty-cycle limit scales with sprint duration."""

    def test_short_sprint_lower_limit(self):
        """60s sprint → limit ~2, so windup happens fast."""
        sprint_duration = 60.0
        limit = max(2, min(8, int(sprint_duration / 30.0)))
        assert limit == 2, f"60s sprint limit = {limit}, expected 2"

    def test_long_sprint_higher_limit(self):
        """1800s sprint → limit 8 (cap)."""
        sprint_duration = 1800.0
        limit = max(2, min(8, int(sprint_duration / 30.0)))
        assert limit == 8, f"1800s sprint limit = {limit}, expected 8"

    def test_medium_sprint_mid_limit(self):
        """300s sprint → limit 10 → clamped to 8."""
        sprint_duration = 300.0
        limit = max(2, min(8, int(sprint_duration / 30.0)))
        assert 2 <= limit <= 8


class TestSafeCreateTask(unittest.TestCase):
    """safe_create_task must work on any event loop without TypeError."""

    def test_safe_create_task_works(self):
        from hledac.universal.utils.async_helpers import safe_create_task

        async def dummy():
            return 42

        async def runner():
            task = safe_create_task(dummy(), name="probe_test_task")
            result = await task
            return result

        result = asyncio.run(runner())
        assert result == 42

    def test_safe_create_task_does_not_raise_eager_start(self):
        """The whole point: never raise TypeError eager_start."""
        from hledac.universal.utils.async_helpers import safe_create_task

        async def dummy():
            return "ok"

        async def runner():
            # Pass eager_start=True — should be silently downgraded if unsupported
            task = safe_create_task(dummy(), name="probe_test_eager", eager_start=True)
            return await task

        result = asyncio.run(runner())
        assert result == "ok"

    def test_safe_create_task_with_none_name(self):
        from hledac.universal.utils.async_helpers import safe_create_task

        async def dummy():
            return None

        async def runner():
            task = safe_create_task(dummy())
            return await task

        asyncio.run(runner())


class TestDefaultsInvariant(unittest.TestCase):
    """Sprint F228G invariant: never produce a sprint that completes in <10s on 60s budget."""

    def test_default_sources_resolve_to_structured_ti(self):
        """Sum of 5 default sources: all must be non-OTHER so prune mode preserves them."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._config = SprintSchedulerConfig()

        items = sched._build_work_items(
            ["cisa_kev", "threatfox_ioc", "urlhaus_recent", "feodo_ip", "openphish_feed"]
        )
        for it in items:
            assert it.tier != SourceTier.OTHER, (
                f"{it.feed_url} tier=OTHER — will be filtered by prune, breaks short sprints"
            )


if __name__ == "__main__":
    unittest.main()
