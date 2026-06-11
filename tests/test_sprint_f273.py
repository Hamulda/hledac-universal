"""F273: Root-cause fixes for `terminal:remaining_too_low` + M1 8GB cutting-edge hygiene.

This test file covers all 8 sub-fixes of Sprint F273:

  F273A — Dynamic branch floor (kills `terminal:remaining_too_low` in windup)
  F273B — Windup ratio 0.20 + adaptive by cycle size
  F273C — Pattern extraction decoupled from branch lifecycle
  F273D — --force-hermes CLI + SprintFlags.hermes_force + diagnostic
  F273E — aiofiles for hot file I/O (streaming exporter)
  F273F — F_NOCACHE for runtime artifacts (LMDB/DuckDB)
  F273G — malloc_zone_pressure_relief per-sprint
  F273H — SprintSchedulerResult diagnostic fields

All tests are hermetic (no network, no MLX, no M1-only code paths). They
exercise the F273 changes via direct class imports + a small mock scheduler
for the helper methods.

Pattern follows tests/test_f250_dynamic_windup.py + tests/test_sprint_f272.py.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import platform
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Test infra: import the modules we need (no pytest fixtures, hermetic)
# ---------------------------------------------------------------------------

def _import_sprint_scheduler_config():
    """Import SprintSchedulerConfig without triggering full module side effects."""
    from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig
    return SprintSchedulerConfig


def _import_min_branch():
    """Import SprintScheduler so we can call _min_branch_remaining_s without
    instantiating the full scheduler (which would touch LMDB/DuckDB)."""
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler
    return SprintScheduler


# ===========================================================================
# F273A: Dynamic branch floor
# ===========================================================================

class TestF273ADynamicBranchFloor(unittest.TestCase):
    """F273A: _MIN_BRANCH_REMAINING_S is now dynamic via _min_branch_remaining_s()."""

    def test_default_floor_is_2_seconds(self):
        """The class-level default must be 2.0s (was 5.0s in pre-F273A)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        self.assertEqual(cfg._MIN_BRANCH_REMAINING_S, 2.0)
        self.assertEqual(cfg._MIN_BRANCH_REMAINING_S_DEFAULT, 2.0)
        self.assertEqual(cfg._MIN_BRANCH_REMAINING_S_CAP, 9.0)

    def test_min_branch_remaining_s_floor_when_no_cycles_seen(self):
        """When _cycle_time_ema is 0 (pre-loop), returns the default 2.0s floor."""
        SprintScheduler = _import_min_branch()
        # Build a minimal stand-in object that has the method (no full ctor).
        instance = SprintScheduler.__new__(SprintScheduler)
        instance._cycle_time_ema = 0.0
        # Use a config to access the constants
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        instance._config = cfg
        self.assertEqual(instance._min_branch_remaining_s(), 2.0)

    def test_min_branch_remaining_s_scales_with_cycle_ema(self):
        """Floor scales with observed cycle time: 0.3 * cycle_ema, clamped [2, 9]."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        for ema, expected_floor in [
            (1.0, 2.0),    # initial default
            (5.0, 2.0),    # clamped to floor
            (10.0, 3.0),   # 0.3 * 10 = 3.0
            (20.0, 6.0),   # 0.3 * 20 = 6.0
            (30.0, 9.0),   # 0.3 * 30 = 9.0 = cap
            (60.0, 9.0),   # saturated at cap
        ]:
            instance = SprintScheduler.__new__(SprintScheduler)
            instance._cycle_time_ema = ema
            instance._config = cfg
            self.assertEqual(
                instance._min_branch_remaining_s(),
                expected_floor,
                f"cycle_ema={ema} should give floor={expected_floor}",
            )

    def test_min_branch_remaining_s_bounded_2_to_9(self):
        """Floor is always in [2.0, 9.0] for any non-negative cycle_ema."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        for ema in (0.0, 0.5, 1.0, 3.0, 10.0, 50.0, 100.0, 1000.0):
            instance = SprintScheduler.__new__(SprintScheduler)
            instance._cycle_time_ema = ema
            instance._config = cfg
            floor = instance._min_branch_remaining_s()
            self.assertGreaterEqual(floor, 2.0, f"ema={ema} below floor")
            self.assertLessEqual(floor, 9.0, f"ema={ema} above cap")

    def test_branch_timeout_returns_zero_only_below_dynamic_floor(self):
        """_branch_timeout_s returns 0 only when remaining_s <= dynamic floor."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        # cycle_ema=10s -> floor=3s
        instance = SprintScheduler.__new__(SprintScheduler)
        instance._cycle_time_ema = 10.0
        instance._config = cfg
        # 2.9s remaining -> below floor (3s) -> 0
        self.assertEqual(instance._branch_timeout_s("PUBLIC", 2.9), 0.0)
        # 3.1s remaining -> above floor -> positive timeout
        self.assertGreater(instance._branch_timeout_s("PUBLIC", 3.1), 0.0)


# ===========================================================================
# F273B: Windup ratio 0.20 + adaptive
# ===========================================================================

class TestF273BWindupRatio(unittest.TestCase):
    """F273B: effective_windup_lead_s now uses 0.20 ratio, clamped [20, 90]."""

    def test_windup_ratio_is_20_percent(self):
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        # 0.20 * 300 = 60, within [20, 90] so exact 60.
        self.assertEqual(cfg.effective_windup_lead_s, 60.0)

    def test_windup_60s_uses_floor_20(self):
        """60s sprint: 0.20*60=12, clamped up to 20."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 20.0)

    def test_windup_120s_no_floor(self):
        """120s sprint: 0.20*120=24, no clamp."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 24.0)

    def test_windup_1800s_capped_at_90(self):
        """1800s sprint: 0.20*1800=360, capped at 90."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 90.0)

    def test_windup_600s_uses_ceiling_90(self):
        """600s sprint: 0.20*600=120, clamped down to 90."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 90.0)

    def test_windup_30s_respects_20_floor(self):
        """30s sprint: 0.20*30=6, clamped up to 20 (floor)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=30)
        self.assertEqual(cfg.effective_windup_lead_s, 20.0)

    def test_windup_for_cycle_no_bonus_when_quick(self):
        """Cycle EMA <= 8s gives no adaptive bonus."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        for ema in (0.0, 1.0, 5.0, 8.0):
            self.assertEqual(
                cfg.windup_for_cycle(ema),
                cfg.effective_windup_lead_s,
                f"quick cycle_ema={ema} should not add bonus",
            )

    def test_windup_for_cycle_adaptive_bonus(self):
        """Slow cycles get +0.5s per s over 8s, capped at +30s."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)  # base=60
        # cycle_ema=20s -> bonus = 0.5 * (20 - 8) = 6s -> total 66s
        self.assertEqual(cfg.windup_for_cycle(20.0), 66.0)
        # cycle_ema=68s -> bonus = 0.5 * 60 = 30 (capped) -> total 90 (ceiling)
        self.assertEqual(cfg.windup_for_cycle(68.0), 90.0)
        # cycle_ema=200s -> bonus capped at 30 -> total 90
        self.assertEqual(cfg.windup_for_cycle(200.0), 90.0)

    def test_windup_for_cycle_floor_protects_short_sprints(self):
        """Short sprint (60s, base=20) keeps a usable active window under adapt."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)  # base=20
        # cycle_ema=30s -> bonus = 0.5*22=11s -> total 31s, active=29s
        self.assertEqual(cfg.windup_for_cycle(30.0), 31.0)
        self.assertEqual(cfg.sprint_duration_s - cfg.windup_for_cycle(30.0), 29.0)

    def test_windup_for_cycle_negative_ema_returns_base(self):
        """Negative cycle EMA (defensive) returns base — fail-safe."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        self.assertEqual(cfg.windup_for_cycle(-1.0), 60.0)


# ===========================================================================
# F273C: Pattern extraction drain registry
# ===========================================================================

class TestF273CPatternExtractionDrain(unittest.TestCase):
    """F273C: schedule_html_extraction + drain_pending_extractions in public_fetcher."""

    def setUp(self):
        # Lazy import to avoid module-load side effects
        from hledac.universal.fetching import public_fetcher
        # Reset the module-level registry between tests
        public_fetcher._DRAIN_REGISTRY.clear()
        public_fetcher._DRAIN_TOTAL_SCHEDULED = 0
        public_fetcher._DRAIN_TOTAL_COMPLETED = 0

    def test_drain_registry_starts_empty(self):
        from hledac.universal.fetching import public_fetcher
        stats = public_fetcher.get_drain_stats()
        self.assertEqual(stats["registry_size"], 0)
        self.assertEqual(stats["total_scheduled"], 0)

    def test_schedule_html_extraction_returns_future(self):
        from hledac.universal.fetching import public_fetcher
        fut = public_fetcher.schedule_html_extraction(
            "<html><body>test IOC</body></html>", "https://example.com",
        )
        self.assertIsNotNone(fut)
        stats = public_fetcher.get_drain_stats()
        self.assertEqual(stats["registry_size"], 1)
        self.assertEqual(stats["total_scheduled"], 1)

    def test_drain_completes_pending_futures(self):
        from hledac.universal.fetching import public_fetcher

        async def _run_drain():
            for i in range(3):
                public_fetcher.schedule_html_extraction(
                    f"<html><body>IOC {i}</body></html>", f"https://x.com/{i}",
                )
            stats = public_fetcher.get_drain_stats()
            assert stats["registry_size"] == 3
            completed, timed_out, elapsed = await public_fetcher.drain_pending_extractions(
                deadline_s=5.0,
            )
            return completed, timed_out, elapsed

        completed, timed_out, elapsed = asyncio.run(_run_drain())
        # All 3 should complete (CPU_EXECUTOR has available workers)
        self.assertGreaterEqual(completed, 3)
        self.assertEqual(timed_out, 0)

    def test_drain_stats_monotonic_counters(self):
        from hledac.universal.fetching import public_fetcher

        async def _run_drain():
            # Schedule inside the same loop that will drain (otherwise the
            # Future lives in a different loop's executor and the drain
            # never picks it up).
            public_fetcher.schedule_html_extraction("<html><body>x</body></html>", "u1")
            public_fetcher.schedule_html_extraction("<html><body>y</body></html>", "u2")
            stats = public_fetcher.get_drain_stats()
            assert stats["total_scheduled"] == 2
            completed, timed_out, _ = await public_fetcher.drain_pending_extractions(
                deadline_s=2.0,
            )
            return completed, timed_out, public_fetcher.get_drain_stats()

        completed, timed_out, stats = asyncio.run(_run_drain())
        self.assertGreaterEqual(completed, 2)
        self.assertEqual(timed_out, 0)
        self.assertEqual(stats["registry_size"], 0)
        self.assertEqual(stats["total_scheduled"], 2)

    def test_drain_bounded_capacity(self):
        """Registry maxlen=512 — overflow drops oldest (with cancel)."""
        from hledac.universal.fetching import public_fetcher
        # Pre-fill beyond capacity (simulate via the same code path used in prod)
        from hledac.universal.fetching.public_fetcher import _DRAIN_REGISTRY
        # Direct cap test: ensure maxlen is set
        self.assertEqual(_DRAIN_REGISTRY.maxlen, 512)

    def test_drain_zero_deadline_returns_immediately(self):
        """Drain with deadline=0 returns (0, 0, 0.0) when no work is pending."""
        from hledac.universal.fetching import public_fetcher
        completed, timed_out, elapsed = asyncio.run(
            public_fetcher.drain_pending_extractions(deadline_s=0.0)
        )
        self.assertEqual((completed, timed_out, elapsed), (0, 0, 0.0))


# ===========================================================================
# F273D: --force-hermes CLI + SprintFlags.hermes_force
# ===========================================================================

class TestF273DForceHermes(unittest.TestCase):
    """F273D: hermes_force flag wires through SprintFlags -> SprintScheduler."""

    def test_sprint_flags_has_hermes_force_field(self):
        """SprintFlags must have a hermes_force:bool field, default False."""
        from hledac.universal.core.__main__ import SprintFlags
        flags = SprintFlags()
        self.assertTrue(hasattr(flags, "hermes_force"))
        self.assertFalse(flags.hermes_force)

    def test_sprint_flags_hermes_force_constructible(self):
        """SprintFlags(hermes_force=True) must work without breaking other fields."""
        from hledac.universal.core.__main__ import SprintFlags
        flags = SprintFlags(
            force=True,
            no_communication=True,
            no_stealth=False,
            no_ghost=False,
            no_coordination=True,
            production=False,
            hermes_force=True,
        )
        self.assertTrue(flags.force)
        self.assertTrue(flags.no_communication)
        self.assertFalse(flags.no_stealth)
        self.assertFalse(flags.no_ghost)
        self.assertTrue(flags.no_coordination)
        self.assertFalse(flags.production)
        self.assertTrue(flags.hermes_force)

    def test_sprint_flags_is_frozen(self):
        """SprintFlags is frozen msgspec.Struct — hermes_force must be immutable."""
        from hledac.universal.core.__main__ import SprintFlags
        flags = SprintFlags(hermes_force=True)
        with self.assertRaises(Exception):  # FrozenInstanceError or AttributeError
            flags.hermes_force = False  # type: ignore[misc]

    def test_sprint_scheduler_accepts_flags_param(self):
        """SprintScheduler.__init__ signature must include flags kwarg."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        import inspect
        sig = inspect.signature(SprintScheduler.__init__)
        self.assertIn("flags", sig.parameters)
        self.assertEqual(sig.parameters["flags"].default, None)

    def test_sprint_scheduler_result_has_hermes_diagnostic_fields(self):
        """SprintSchedulerResult must have hermes_model_loaded etc. with sane defaults."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertFalse(result.hermes_model_loaded)
        self.assertFalse(result.hermes_load_attempted)
        self.assertEqual(result.hermes_load_reason, "")
        self.assertEqual(result.hermes_load_elapsed_s, 0.0)


# ===========================================================================
# F273E: aiofiles in streaming exporter
# ===========================================================================

class TestF273EAiofilesStreamingExporter(unittest.TestCase):
    """F273E: streaming_exporter._write_section uses aiofiles (with sync fallback)."""

    def test_streaming_exporter_imports_cleanly(self):
        """The module must import without errors even on minimal installs."""
        from hledac.universal.export.components import streaming_exporter
        self.assertTrue(hasattr(streaming_exporter, "export_sprint_streaming"))
        self.assertTrue(hasattr(streaming_exporter, "SprintStreamingResult"))

    def test_write_section_uses_aiofiles_when_available(self):
        """If aiofiles is available, _write_section uses async with aiofiles.open."""
        from hledac.universal.export.components import streaming_exporter
        import inspect
        src = inspect.getsource(streaming_exporter)
        self.assertIn("aiofiles", src)
        self.assertIn("async with _f273e_aiofiles.open", src)
        self.assertIn("ImportError", src)  # fallback path


# ===========================================================================
# F273F: F_NOCACHE for runtime artifacts
# ===========================================================================

class TestF273FFnocacheRuntimeArtifacts(unittest.TestCase):
    """F273F: apply_nocache_to_path for LMDB / DuckDB / telemetry artifacts."""

    def test_fnocache_constant_present(self):
        from hledac.universal.tools.file_cache import F_NOCACHE
        if platform.system() == "Darwin":
            self.assertEqual(F_NOCACHE, 48)
        else:
            self.assertIsNone(F_NOCACHE)

    def test_apply_nocache_to_path_returns_bool(self):
        from hledac.universal.tools.file_cache import apply_nocache_to_path
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            rc = apply_nocache_to_path(path)
            # On non-Darwin, always False. On Darwin, True if file exists.
            if platform.system() == "Darwin":
                self.assertTrue(rc)
            else:
                self.assertFalse(rc)
        finally:
            os.unlink(path)

    def test_apply_nocache_below_threshold_returns_false(self):
        """Below NOCACHE_THRESHOLD_BYTES the call is a no-op (False)."""
        from hledac.universal.tools.file_cache import (
            NOCACHE_THRESHOLD_BYTES, apply_nocache_to_path,
        )
        if platform.system() != "Darwin":
            self.skipTest("F_NOCACHE only on Darwin")
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            rc = apply_nocache_to_path(path, content_length=1024)  # 1KB
            self.assertFalse(rc)  # below 50MB threshold
        finally:
            os.unlink(path)

    def test_apply_nocache_missing_file_returns_false(self):
        """If file doesn't exist, returns False (fail-soft)."""
        from hledac.universal.tools.file_cache import apply_nocache_to_path
        rc = apply_nocache_to_path("/nonexistent/path/to/file.db")
        self.assertFalse(rc)

    def test_tools_init_exports_apply_nocache_to_path(self):
        """tools/__init__.py must export apply_nocache_to_path for canonical import."""
        from hledac.universal.tools import apply_nocache_to_path
        self.assertTrue(callable(apply_nocache_to_path))


# ===========================================================================
# F273G: malloc_zone_pressure_relief per-sprint
# ===========================================================================

class TestF273GMallocPressureRelief(unittest.TestCase):
    """F273G: _maybe_call_pressure_relief wired into pre-windup barrier."""

    def test_malloc_zone_pressure_relief_importable(self):
        from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
        self.assertTrue(callable(malloc_zone_pressure_relief))

    def test_malloc_zone_pressure_relief_returns_int(self):
        from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
        rc = malloc_zone_pressure_relief()
        self.assertIsInstance(rc, int)
        self.assertGreaterEqual(rc, 0)  # 0 on non-Darwin or no-op

    def test_sprint_scheduler_result_has_pressure_relief_fields(self):
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertEqual(result.malloc_pressure_relief_count, 0)
        self.assertEqual(result.malloc_pressure_relief_last_rc, 0)
        self.assertEqual(result.malloc_pressure_relief_last_at_s, 0.0)

    def test_maybe_call_pressure_relief_method_exists(self):
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        self.assertTrue(hasattr(SprintScheduler, "_maybe_call_pressure_relief"))

    def test_maybe_call_pressure_relief_increments_counter(self):
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler, SprintSchedulerConfig, SprintSchedulerResult,
        )
        instance = SprintScheduler.__new__(SprintScheduler)
        instance._config = SprintSchedulerConfig(sprint_duration_s=60)
        instance._result = SprintSchedulerResult()
        instance._maybe_call_pressure_relief()
        # Counter incremented regardless of platform
        self.assertEqual(instance._result.malloc_pressure_relief_count, 1)


# ===========================================================================
# F273H: SprintSchedulerResult diagnostic fields (cross-cutting)
# ===========================================================================

class TestF273HResultDiagnostics(unittest.TestCase):
    """F273H: All F273 result fields are present with correct defaults."""

    def test_pattern_extraction_drain_fields(self):
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertEqual(result.pattern_extraction_drain_completed, 0)
        self.assertEqual(result.pattern_extraction_drain_timed_out, 0)
        self.assertEqual(result.pattern_extraction_drain_elapsed_s, 0.0)

    def test_hermes_diagnostic_fields(self):
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertFalse(result.hermes_model_loaded)
        self.assertFalse(result.hermes_load_attempted)
        self.assertEqual(result.hermes_load_reason, "")
        self.assertEqual(result.hermes_load_elapsed_s, 0.0)

    def test_dynamic_branch_floor_field(self):
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertEqual(result.dynamic_branch_floor_s, 0.0)

    def test_windup_lead_diagnostic_fields(self):
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        self.assertEqual(result.effective_windup_lead_used_s, 0.0)
        self.assertEqual(result.windup_lead_adaptive_factor, 1.0)


# ===========================================================================
# F273I: Integration — does the windup ratio change break existing tests?
# ===========================================================================

class TestF273IBackwardCompat(unittest.TestCase):
    """F273I: Old F250/F272A tests must be updated to match the 0.20 contract.

    This class re-asserts the new F273B values so we can catch any future
    regression of the windup formula. The original test_f250_dynamic_windup.py
    is updated separately (test_f273_amends_f250.py).
    """

    def test_f273b_replaces_f272a_amendment(self):
        """F273B: 0.20 ratio with [20, 90] clamp replaces F272A's 0.10 with [15, 60]."""
        # Pre-F272A: 30% / [30, 180]
        # F272A: 10% / [15, 60]
        # F273B: 20% / [20, 90]
        for dur, expected in [
            (60, 20.0),   # floor
            (150, 30.0),  # 0.20 * 150
            (300, 60.0),  # 0.20 * 300
            (450, 90.0),  # 0.20 * 450 = 90, no clamp
            (600, 90.0),  # 0.20 * 600 = 120 -> 90 (ceiling)
            (1800, 90.0), # 0.20 * 1800 = 360 -> 90 (ceiling)
        ]:
            cfg = _import_sprint_scheduler_config()(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s, expected,
                f"F273B: dur={dur} expected={expected}, got {cfg.effective_windup_lead_s}",
            )

    def test_drain_helpers_importable(self):
        """drain_pending_extractions + get_drain_stats are importable."""
        from hledac.universal.fetching.public_fetcher import (
            drain_pending_extractions, get_drain_stats, schedule_html_extraction,
        )
        self.assertTrue(callable(drain_pending_extractions))
        self.assertTrue(callable(get_drain_stats))
        self.assertTrue(callable(schedule_html_extraction))


if __name__ == "__main__":
    unittest.main()
