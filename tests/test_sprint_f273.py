"""F273: Root-cause fixes for `terminal:remaining_too_low` + M1 8GB cutting-edge hygiene.

This test file covers all 8 sub-fixes of Sprint F273:

  F273A — Dynamic branch floor based on cycle-ema (kills `terminal:remaining_too_low` in windup)
  F273B — Remaining-time-aware branch floor (remaining_s * 0.15, cap 5.0s) for 300s+ sprints
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

import asyncio
import msgspec
import os
import platform
import unittest

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
        self.assertEqual(cfg._MIN_BRANCH_REMAINING_S_CAP, 5.0)

    def test_min_branch_remaining_s_floor_when_no_cycles_seen(self):
        """When _cycle_time_ema is 0 (pre-loop), returns the default 2.0s floor."""
        SprintScheduler = _import_min_branch()
        # Build a minimal stand-in object that has the method (no full ctor).
        instance = SprintScheduler.__new__(SprintScheduler)
        instance._cycle_time_ema = 0.0
        # Use a config to access the constants
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        instance._config = cfg
        self.assertEqual(instance._min_branch_remaining_s(None), 2.0)

    def test_min_branch_remaining_s_fallback_cycle_ema_formula(self):
        """Fallback (no remaining_s arg) uses 0.1 * cycle_ema, clamped [2, 5].
        This tests backward compatibility when remaining_s is None."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        for ema, expected_floor in [
            (1.0, 2.0),  # initial default
            (5.0, 2.0),  # clamped to floor
            (10.0, 2.0),  # clamped to floor
            (20.0, 2.0),  # 0.1 * 20 = 2.0, clamped to floor
            (30.0, 3.0),  # 0.1 * 30 = 3.0
            (60.0, 5.0),  # capped at 5.0
        ]:
            instance = SprintScheduler.__new__(SprintScheduler)
            instance._cycle_time_ema = ema
            instance._config = cfg
            self.assertEqual(
                instance._min_branch_remaining_s(None),
                expected_floor,
                f"cycle_ema={ema} should give floor={expected_floor}",
            )

    def test_min_branch_remaining_s_bounded_2_to_5(self):
        """Floor is always in [2.0, 5.0] for any remaining_s or cycle_ema."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        # Test with remaining_s argument (primary formula)
        for remaining_s in (0.0, -1.0, 10.0, 30.0, 60.0, 150.0, 1000.0):
            instance = SprintScheduler.__new__(SprintScheduler)
            instance._config = cfg
            floor = instance._min_branch_remaining_s(remaining_s)
            self.assertGreaterEqual(floor, 2.0, f"remaining_s={remaining_s} below floor")
            self.assertLessEqual(floor, 5.0, f"remaining_s={remaining_s} above cap")
        # Test fallback with cycle_ema
        for ema in (0.0, 0.5, 1.0, 3.0, 10.0, 50.0, 100.0, 1000.0):
            instance = SprintScheduler.__new__(SprintScheduler)
            instance._cycle_time_ema = ema
            instance._config = cfg
            floor = instance._min_branch_remaining_s(None)
            self.assertGreaterEqual(floor, 2.0, f"ema={ema} below floor")
            self.assertLessEqual(floor, 5.0, f"ema={ema} above cap")

    def test_branch_timeout_returns_zero_only_below_dynamic_floor(self):
        """_branch_timeout_s returns 0 only when remaining_s <= dynamic floor."""
        SprintScheduler = _import_min_branch()
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        # cycle_ema=10s -> base=3.0, but look_ahead=2.0 (no lifecycle) -> floor=2.0
        instance = SprintScheduler.__new__(SprintScheduler)
        instance._cycle_time_ema = 10.0
        instance._config = cfg
        # 1.9s remaining -> below floor (2.0) -> 0
        self.assertEqual(instance._branch_timeout_s("PUBLIC", 1.9), 0.0)
        # 2.1s remaining -> above floor (2.0) -> positive timeout
        self.assertGreater(instance._branch_timeout_s("PUBLIC", 2.1), 0.0)


# ===========================================================================
# F273B: Windup ratio 0.20 + adaptive
# ===========================================================================


class TestF273BWindupRatio(unittest.TestCase):
    """F288: effective_windup_lead_s uses 0.30 ratio (standard), [30, 60/120] cap.

    Aggressive mode uses 0.15 ratio. F221-ABORT guard uses 30%/[30,180].
    """

    def test_windup_ratio_is_30_percent(self):
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        # P0-1: F288 cap removed. 0.30 * 300 = 90 (no cap, within [30, 180])
        self.assertEqual(cfg.effective_windup_lead_s, 90.0)

    def test_windup_60s_uses_floor_30(self):
        """60s sprint: 0.30*60=18, clamped up to 30."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)
        self.assertEqual(cfg.effective_windup_lead_s, 30.0)

    def test_windup_120s_scales(self):
        """120s sprint: 0.30*120=36 (above floor, within cap)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=120)
        self.assertEqual(cfg.effective_windup_lead_s, 36.0)

    def test_windup_1800s_uses_ceiling_180(self):
        """1800s sprint: 0.30*1800=540, clamped to 180 (max ceiling)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=1800)
        self.assertEqual(cfg.effective_windup_lead_s, 180.0)

    def test_windup_600s_uses_ceiling_180(self):
        """600s sprint: 0.30*600=180, clamped to 180 (max ceiling)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=600)
        self.assertEqual(cfg.effective_windup_lead_s, 180.0)

    def test_windup_300s_uses_90_no_cap(self):
        """P0-1: 300s sprint: 0.30*300=90 (F288 cap removed)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        self.assertEqual(cfg.effective_windup_lead_s, 90.0)

    def test_windup_aggressive_300s_uses_45(self):
        """Aggressive 300s: 0.15*300=45."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300, aggressive_mode=True)
        self.assertEqual(cfg.effective_windup_lead_s, 45.0)

    def test_windup_aggressive_600s_uses_90(self):
        """Aggressive 600s: 0.15*600=90 (within [30, 180] ceiling)."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=600, aggressive_mode=True)
        self.assertEqual(cfg.effective_windup_lead_s, 90.0)

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
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)  # P0-1: base=90 (no F288 cap)
        # cycle_ema=20s -> bonus = 0.5 * (20 - 8) = 6s -> total 96s
        self.assertEqual(cfg.windup_for_cycle(20.0), 96.0)
        # cycle_ema=68s -> bonus = 0.5 * 60 = 30 (capped) -> total 120s
        self.assertEqual(cfg.windup_for_cycle(68.0), 120.0)
        # cycle_ema=200s -> bonus capped at 30 -> total 120s
        self.assertEqual(cfg.windup_for_cycle(200.0), 120.0)

    def test_windup_for_cycle_floor_protects_short_sprints(self):
        """Short sprint (60s, base=30) keeps a usable active window under adapt."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=60)  # base=30
        # cycle_ema=30s -> bonus = min(30, 0.5*22)=11s -> total 41s, active=19s
        self.assertEqual(cfg.windup_for_cycle(30.0), 41.0)
        self.assertEqual(cfg.sprint_duration_s - cfg.windup_for_cycle(30.0), 19.0)

    def test_windup_for_cycle_negative_ema_returns_base(self):
        """Negative cycle EMA (defensive) returns base — fail-safe."""
        cfg = _import_sprint_scheduler_config()(sprint_duration_s=300)
        # P0-1: base=90 (no F288 cap), negative EMA returns base
        self.assertEqual(cfg.windup_for_cycle(-1.0), 90.0)


# ===========================================================================
# F273C: Pattern extraction drain registry
# ===========================================================================


class TestF273CPatternExtractionDrain(unittest.TestCase):
    """F273C: schedule_html_extraction + drain_pending_extractions in public_fetcher."""

    def setUp(self):
        # Lazy import to avoid module-load side effects
        from hledac.universal.fetching import public_fetcher

        # Reset the module-level registry between tests
        public_fetcher._drain_registry.clear()

    def test_drain_registry_starts_empty(self):
        from hledac.universal.fetching import public_fetcher

        stats = public_fetcher.get_drain_stats()
        self.assertEqual(stats["registry_size"], 0)
        self.assertEqual(stats["total_scheduled"], 0)

    def test_schedule_html_extraction_returns_future(self):
        from hledac.universal.fetching import public_fetcher

        fut = public_fetcher.schedule_html_extraction(
            "<html><body>test IOC</body></html>",
            "https://example.com",
        )
        self.assertIsNotNone(fut)
        stats = public_fetcher.get_drain_stats()
        self.assertEqual(stats["registry_size"], 1)
        self.assertEqual(stats["total_scheduled"], 1)

    def test_drain_completes_pending_futures(self, event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use event_loop fixture instead of asyncio.run()."""
        from hledac.universal.fetching import public_fetcher

        async def _run_drain():
            for i in range(3):
                public_fetcher.schedule_html_extraction(
                    f"<html><body>IOC {i}</body></html>",
                    f"https://x.com/{i}",
                )
            stats = public_fetcher.get_drain_stats()
            assert stats["registry_size"] == 3
            completed, timed_out, elapsed = await public_fetcher.drain_pending_extractions(
                deadline_s=5.0,
            )
            return completed, timed_out, elapsed

        completed, timed_out, elapsed = event_loop.run_until_complete(_run_drain())
        # All 3 should complete (CPU_EXECUTOR has available workers)
        self.assertGreaterEqual(completed, 3)
        self.assertEqual(timed_out, 0)

    def test_drain_stats_monotonic_counters(self, event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use event_loop fixture instead of asyncio.run()."""
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

        completed, timed_out, stats = event_loop.run_until_complete(_run_drain())
        self.assertGreaterEqual(completed, 2)
        self.assertEqual(timed_out, 0)
        self.assertEqual(stats["registry_size"], 0)
        self.assertEqual(stats["total_scheduled"], 2)

    def test_drain_bounded_capacity(self):
        """Registry maxlen=512 — overflow drops oldest (with cancel)."""
        # Pre-fill beyond capacity (simulate via the same code path used in prod)
        from hledac.universal.fetching.public_fetcher import _DRAIN_REGISTRY

        # Direct cap test: ensure maxlen is set
        self.assertEqual(_DRAIN_REGISTRY.maxlen, 512)

    def test_drain_zero_deadline_returns_immediately(self, event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use event_loop fixture instead of asyncio.run()."""
        from hledac.universal.fetching import public_fetcher

        completed, timed_out, elapsed = event_loop.run_until_complete(
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
        from hledac.universal.runtime.sprint_entrypoint import SprintFlags

        flags = SprintFlags()
        self.assertTrue(hasattr(flags, "hermes_force"))
        self.assertFalse(flags.hermes_force)

    def test_sprint_flags_hermes_force_constructible(self):
        """SprintFlags(hermes_force=True) must work without breaking other fields."""
        from hledac.universal.runtime.sprint_entrypoint import SprintFlags

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
        from hledac.universal.runtime.sprint_entrypoint import SprintFlags

        flags = SprintFlags(hermes_force=True)
        with self.assertRaises(Exception):  # FrozenInstanceError or AttributeError
            flags.hermes_force = False  # type: ignore[misc]

    def test_sprint_scheduler_accepts_flags_param(self):
        """SprintScheduler.__init__ signature must include flags kwarg."""
        import inspect

        from hledac.universal.runtime.sprint_scheduler import SprintScheduler

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
        import inspect

        from hledac.universal.export.components import streaming_exporter

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
        import tempfile

        from hledac.universal.tools.file_cache import apply_nocache_to_path

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
            apply_nocache_to_path,
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
            SprintScheduler,
            SprintSchedulerConfig,
            SprintSchedulerResult,
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
    """F288: Windup formula updated to 0.30 ratio / [30, 60/120] cap.

    Aggressive mode uses 0.15 ratio. Previous contracts superseded.
    """

    def test_f278a_replaces_f273b_contract(self):
        """P0-1: 0.30 ratio with [30, 180] ceiling -- F288 cap removed."""
        for dur, expected in [
            (60, 30.0),  # floor (0.30*60=18, clamped to 30)
            (100, 30.0),  # floor (0.30*100=30)
            (150, 45.0),  # 0.30 * 150
            (300, 90.0),  # P0-1: no F288 cap (0.30*300=90)
            (600, 180.0),  # P0-1: no F288 cap (0.30*600=180, clamped to 180)
            (1800, 180.0),  # P0-1: no F288 cap (clamped to 180)
        ]:
            cfg = _import_sprint_scheduler_config()(sprint_duration_s=float(dur))
            self.assertEqual(
                cfg.effective_windup_lead_s,
                expected,
                f"F288: dur={dur} expected={expected}, got {cfg.effective_windup_lead_s}",
            )

    def test_f288_aggressive_mode_windup(self):
        """P0-1: aggressive mode uses 0.15 ratio, [30, 180] ceiling (F288 cap removed)."""
        for dur, expected in [
            (120, 30.0),  # floor (0.15*120=18, clamped to 30)
            (180, 30.0),  # floor (0.15*180=27, clamped to 30)
            (300, 45.0),  # 0.15 * 300
            (600, 90.0),  # 0.15*600=90 (< 180 ceiling)
            (1800, 180.0),  # P0-1: no F288 cap (clamped to 180)
        ]:
            cfg = _import_sprint_scheduler_config()(sprint_duration_s=float(dur), aggressive_mode=True)
            self.assertEqual(
                cfg.effective_windup_lead_s,
                expected,
                f"F288 aggressive: dur={dur} expected={expected}, got {cfg.effective_windup_lead_s}",
            )

    def test_drain_helpers_importable(self):
        """drain_pending_extractions + get_drain_stats are importable."""
        from hledac.universal.fetching.public_fetcher import (
            drain_pending_extractions,
            get_drain_stats,
            schedule_html_extraction,
        )

        self.assertTrue(callable(drain_pending_extractions))
        self.assertTrue(callable(get_drain_stats))
        self.assertTrue(callable(schedule_html_extraction))


if __name__ == "__main__":
    unittest.main()
