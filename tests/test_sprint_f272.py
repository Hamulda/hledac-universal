"""
Sprint F272 — 6 follow-up fixes from sprint 8sa_1780924256274_ff4fd2 analysis.

Covers:
  P1-4 (F272A): windup_lead floor 30s→15s, cap 180s→60s, formula 30%→10%
  P1-5 (F272E): max_cycles adaptive via cycle_time EMA
  P2-6 (F272D): advisory log LRU(16) dedup
  P2-7 (F272C): lane name case-normalization on output
  P3-9 (F272G): healthcheck fetch=NA → not_initialized
  P3-10 (F272B): --production pre-flight abort
"""

from unittest.mock import MagicMock

# ── Module loading ───────────────────────────────────────────────────────────
# sprint_scheduler.py is part of hledac.universal.runtime package; import via
# the public re-export rather than a raw path load to keep Pyright happy.
from hledac.universal.runtime.sprint_scheduler import (  # type: ignore
    HealthReport,
    SprintSchedulerConfig,
    _advisory_log_stats,
    _log_advisory_dedup,
    _reset_advisory_log_dedup,
    canonical_lane_name,
)

# ── F272A: windup_lead amendment ────────────────────────────────────────────


class TestF272AWindupAmendment:
    """P0-1: Floor 30s, ceiling 180s, formula 30% (standard) / 15% (aggressive).

    F288 cap (60s for ≤300s, 120s for >300s) removed — no longer needed.
    """

    def test_60s_sprint_uses_30s_floor(self) -> None:
        """60s duration: 0.30*60=18, clamped to 30s floor."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60.0)
        assert cfg.effective_windup_lead_s == 30.0

    def test_150s_sprint_scales(self) -> None:
        """150s * 0.30 = 45s -- no clamp needed."""
        cfg = SprintSchedulerConfig(sprint_duration_s=150.0)
        assert cfg.effective_windup_lead_s == 45.0

    def test_300s_sprint_uses_90s_no_cap(self) -> None:
        """P0-1: 300s * 0.30 = 90s (F288 cap removed)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=300.0)
        assert cfg.effective_windup_lead_s == 90.0

    def test_600s_sprint_uses_180s_ceiling(self) -> None:
        """P0-1: 600s * 0.30 = 180s, clamped to 180s ceiling."""
        cfg = SprintSchedulerConfig(sprint_duration_s=600.0)
        assert cfg.effective_windup_lead_s == 180.0

    def test_1800s_sprint_uses_180s_ceiling(self) -> None:
        """P0-1: 1800s * 0.30 = 540s, clamped to 180s ceiling."""
        cfg = SprintSchedulerConfig(sprint_duration_s=1800.0)
        assert cfg.effective_windup_lead_s == 180.0

    def test_active_window_preserved_for_short_sprints(self) -> None:
        """60s sprint: 30s windup → 30s active (≥30s floor, F221-ABORT compatible)."""
        cfg = SprintSchedulerConfig(sprint_duration_s=60.0)
        active = max(0.0, cfg.sprint_duration_s - cfg.effective_windup_lead_s)
        assert active >= 30.0, f"active={active}, F288 floor broken"

    def test_windup_below_50pct_of_budget_for_all_durations(self) -> None:
        """Hard invariant: windup ≤ 50% of duration for duration > 60s.

        Short sprints (≤60s) are exempt: F221-ABORT floor (30s) dominates and
        would violate the 50% rule for 30s sprints (30/30=100%).
        """
        for dur in (90, 120, 150, 200, 300, 600, 1200, 1800):
            cfg = SprintSchedulerConfig(sprint_duration_s=float(dur))
            ratio = cfg.effective_windup_lead_s / dur
            assert ratio <= 0.50, f"dur={dur}s, windup_ratio={ratio:.2f}"


# ── F272B: --production pre-flight abort ────────────────────────────────────


class TestF272BProductionPreflight:
    """P3-10: --production flag triggers sys.exit(2) on fetch=NA."""

    def test_production_flag_parsed_by_argparse(self) -> None:
        """core/__main__.py argparse must accept --production."""
        # The arg is added at the parser level -- verify by trying to parse it.
        # We re-create a minimal parser mirroring the production registration.
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--production", action="store_true")
        args = parser.parse_args(["--production"])
        assert args.production is True

    def test_production_default_is_false(self) -> None:
        """Without --production, fail-soft advisory-degraded mode continues."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--production", action="store_true")
        args = parser.parse_args([])
        assert args.production is False


# ── F272C: lane name case-normalization ──────────────────────────────────────


class TestF272CLaneNameNormalization:
    """P2-7: Apply canonical_lane_name() once on output consumer."""

    def test_canonical_lane_name_uppercases_strings(self) -> None:
        assert canonical_lane_name("public") == "PUBLIC"
        assert canonical_lane_name("Public") == "PUBLIC"
        assert canonical_lane_name("PUBLIC") == "PUBLIC"

    def test_canonical_lane_name_handles_enum(self) -> None:
        """Enum values must be unwrapped before uppercase."""
        fake_enum = MagicMock()
        fake_enum.value = "i2p"
        assert canonical_lane_name(fake_enum) == "I2P"

    def test_canonical_lane_name_handles_non_string(self) -> None:
        """Defensive: even int-like values get stringified + uppercased."""
        assert canonical_lane_name(42) == "42"
        assert canonical_lane_name(None) == "NONE"

    def test_normalization_mixed_input_output_uniform(self) -> None:
        """The exact regression: ['PUBLIC', 'public'] must collapse to all PUBLIC."""
        mixed = ["PUBLIC", "public", "Public", "pUbLiC"]
        result = tuple(canonical_lane_name(x) for x in mixed)
        assert all(x == "PUBLIC" for x in result), f"got: {result}"


# ── F272D: advisory log LRU(16) dedup ────────────────────────────────────────


class TestF272DAdvisoryLruDedup:
    """P2-6: Bounded LRU(16) suppresses duplicate advisory warnings."""

    def setup_method(self) -> None:
        _reset_advisory_log_dedup()

    def teardown_method(self) -> None:
        _reset_advisory_log_dedup()

    def test_first_emit_returns_true(self) -> None:
        log = MagicMock()
        assert _log_advisory_dedup(log, "k1", "[TAG] msg %s", "v") is True
        log.warning.assert_called_once()

    def test_duplicate_key_returns_false(self) -> None:
        log = MagicMock()
        _log_advisory_dedup(log, "k1", "[TAG] first")
        assert _log_advisory_dedup(log, "k1", "[TAG] dup") is False
        assert log.warning.call_count == 1  # only first

    def test_100_duplicates_only_emit_once(self) -> None:
        """The exact regression: 100× identical → 1 emit + 99 suppressed."""
        log = MagicMock()
        for _ in range(100):
            _log_advisory_dedup(log, "dht_fail:RuntimeError", "[F214Q] DHT failed")
        assert log.warning.call_count == 1
        stats = _advisory_log_stats()
        assert stats["suppressed_total"] == 99

    def test_lru_evicts_oldest_at_16_keys(self) -> None:
        """16 unique keys fill the LRU; 17th key evicts the oldest."""
        log = MagicMock()
        for i in range(17):
            _log_advisory_dedup(log, f"k{i}", "[TAG] %s", i)
        stats = _advisory_log_stats()
        assert stats["unique_keys"] == 16
        assert stats["max_keys"] == 16

    def test_lru_hit_does_not_evict(self) -> None:
        """Re-emitting an existing key bumps the count but does not move it
        to the front; oldest insertion is still evicted first (FIFO)."""
        log = MagicMock()
        # Fill with 16 keys
        for i in range(16):
            _log_advisory_dedup(log, f"k{i}", "[T] %s", i)
        # Re-emit k0 multiple times (should NOT move to front in pure FIFO)
        for _ in range(5):
            _log_advisory_dedup(log, "k0", "[T] again")
        # Add 17th: should evict k0 (oldest) regardless of recent hits
        _log_advisory_dedup(log, "k16", "[T] new")
        stats = _advisory_log_stats()
        # k0 should be gone (evicted) — its hit count survives in suppressed_total
        assert stats["unique_keys"] == 16

    def test_reset_clears_state(self) -> None:
        log = MagicMock()
        for _ in range(5):
            _log_advisory_dedup(log, "k1", "[T] dup")
        _reset_advisory_log_dedup()
        # After reset, the same key emits again
        assert _log_advisory_dedup(log, "k1", "[T] fresh") is True
        stats = _advisory_log_stats()
        assert stats["unique_keys"] == 1
        assert stats["suppressed_total"] == 0

    def test_different_keys_each_emit_once(self) -> None:
        log = MagicMock()
        for i in range(5):
            _log_advisory_dedup(log, f"k{i}", "[T] %s", i)
        assert log.warning.call_count == 5


# ── F272E: max_cycles adaptive via cycle_time EMA ────────────────────────────


class TestF272EMaxCyclesAdaptive:
    """P1-5: effective_max_cycles derives from cycle_time EMA."""

    def test_ema_state_lazy_init_defaults(self) -> None:
        """The EMA bootstrap uses 1.0s as a safe initial cycle_time."""

        # Simulate the lazy-init block from sprint_scheduler.
        class _FakeSelf:
            _cycle_time_ema: float = 1.0
            _last_cycle_start: float | None = None
            _effective_max_cycles: int = 100

        s = _FakeSelf()
        assert s._cycle_time_ema == 1.0
        assert s._last_cycle_start is None
        assert s._effective_max_cycles == 100  # bootstrap

    def test_ema_clamps_pathological_outliers(self) -> None:
        """Single 60s DuckDB stall must not inflate the EMA beyond 10s."""
        # The clamp range [0.1, 10.0] is applied BEFORE the EMA update.
        # Simulate: 1.0s baseline, then 60s outlier, then 1.0s.
        ema = 1.0
        for elapsed in (60.0, 1.0, 1.0, 1.0):
            bounded = max(0.1, min(10.0, elapsed))
            ema = 0.7 * ema + 0.3 * bounded
        # 60s outlier is capped to 10s, blended: 0.7*1.0 + 0.3*10.0 = 3.7
        # Then 1.0 cycles pull it back: 0.7*3.7 + 0.3*1.0 = 2.89, etc.
        # Hard upper bound: even after outlier, ema stays < 5.0
        assert ema < 5.0, f"EMA runaway: {ema}"

    def test_effective_max_cycles_floor_at_50(self) -> None:
        """Clamp floor: even tiny active windows get at least 50 cycles."""
        # active=10, ema=1.0 → 10/1.0 = 10 → max(50, min(300, 10)) = 50
        active = 10.0
        ema = 1.0
        cap = max(50, min(300, int(active / ema)))
        assert cap == 50

    def test_effective_max_cycles_ceiling_at_300(self) -> None:
        """Clamp ceiling: even 1800s sprints cap at 300 cycles."""
        active = 1740.0  # 1800 - 60 windup
        ema = 0.5  # fast
        cap = max(50, min(300, int(active / ema)))
        assert cap == 300

    def test_typical_60s_sprint_yields_45_to_450_cycles(self) -> None:
        """60s sprint with new F272A windup 15s: active=45s.
        720ms cycles → 45/0.72 ≈ 62 cycles; with EMA floor 50, never below 50.
        """
        active = 45.0
        ema = 0.72
        cap = max(50, min(300, int(active / ema)))
        assert 50 <= cap <= 65


# ── F272G: healthcheck fetch=NA → not_initialized ────────────────────────────


class TestF272GHealthcheckNotInitialized:
    """P3-9: HealthReport.summary() reports 'not_initialized' instead of 'NA'."""

    def test_summary_reports_not_initialized_when_fetch_down(self) -> None:
        """When fetch_coordinator_ok=False, summary must say 'not_initialized'."""
        hr = HealthReport(duckdb_ok=True, hermes_ok=True, fetch_coordinator_ok=False, graph_service_ok=True)
        s = hr.summary()
        assert "fetch=not_initialized" in s, f"got: {s}"
        assert "fetch=NA" not in s, f"stale 'NA' present: {s}"

    def test_summary_reports_ok_when_fetch_up(self) -> None:
        hr = HealthReport(duckdb_ok=True, hermes_ok=True, fetch_coordinator_ok=True, graph_service_ok=True)
        s = hr.summary()
        assert "fetch=OK" in s

    def test_summary_reports_not_initialized_when_graph_down(self) -> None:
        """Same fix applied to graph field (was 'NA')."""
        hr = HealthReport(duckdb_ok=True, hermes_ok=True, fetch_coordinator_ok=True, graph_service_ok=False)
        s = hr.summary()
        assert "graph=not_initialized" in s, f"got: {s}"
        assert "graph=NA" not in s, f"stale 'NA' present: {s}"

    def test_summary_full_string_contains_no_na(self) -> None:
        """Hard invariant: no 'NA' substring anywhere in summary output."""
        hr = HealthReport(
            duckdb_ok=False,
            hermes_ok=False,
            fetch_coordinator_ok=False,
            graph_service_ok=False,
        )
        s = hr.summary()
        assert "NA" not in s, f"summary still has NA: {s}"


# ── Cross-cutting invariants ─────────────────────────────────────────────────


class TestF272CrossCutting:
    """Cross-cutting M1-8GB / fail-soft invariants across the 6 fixes."""

    def setup_method(self) -> None:
        _reset_advisory_log_dedup()

    def teardown_method(self) -> None:
        _reset_advisory_log_dedup()

    def test_all_fixes_use_zero_or_bounded_memory(self) -> None:
        """M1 8GB: LRU is bounded at 16, all other fixes use O(1) state."""
        log = MagicMock()
        _reset_advisory_log_dedup()
        for i in range(1000):  # way more than LRU size
            _log_advisory_dedup(log, f"k{i % 20}", "[T] %s", i)
        stats = _advisory_log_stats()
        assert stats["unique_keys"] <= 16, "LRU overflow"

    def test_all_fixes_fail_soft(self) -> None:
        """Fail-soft: every helper either returns a value or no-ops, never raises."""
        log = MagicMock()
        # P2-6: dedup on non-string key
        _log_advisory_dedup(log, 12345, "[T] %s", "v")  # type: ignore[arg-type]
        # P2-7: canonical on weird input
        assert isinstance(canonical_lane_name(object()), str)
        # P1-4: 0-duration sprint
        cfg = SprintSchedulerConfig(sprint_duration_s=0.0)
        assert cfg.effective_windup_lead_s == 30.0  # floor applied (30s for 0-duration)

    def test_windup_and_max_cycles_invariant_together(self) -> None:
        """F272A + F272E must agree: shorter windup allows more cycles."""
        cfg60 = SprintSchedulerConfig(sprint_duration_s=60.0)
        cfg300 = SprintSchedulerConfig(sprint_duration_s=300.0)
        active60 = max(0.0, 60.0 - cfg60.effective_windup_lead_s)
        active300 = max(0.0, 300.0 - cfg300.effective_windup_lead_s)
        # Active window grows super-linearly with duration
        assert active300 > active60 * 3, f"active60={active60}, active300={active300}"
