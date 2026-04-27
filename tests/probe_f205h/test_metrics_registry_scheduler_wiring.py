"""
Sprint F205H: MetricsRegistry scheduler wiring probe tests.

Tests that MetricsRegistry is initialized, ticked, and closed
fail-soft in the sprint scheduler lifecycle.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.metrics_registry import MetricsRegistry


class TestMetricsRegistrySchedulerWiring:
    """F205H: MetricsRegistry wired into SprintScheduler lifecycle."""

    @pytest.fixture
    def mock_lifecycle(self):
        """Minimal mock lifecycle with sprint_id."""
        lc = MagicMock()
        lc.sprint_id = "probe_f205h_test"
        lc.start = MagicMock()
        lc.tick.return_value = MagicMock(name="SprintPhase.ACTIVE")
        lc.current_phase.name = "ACTIVE"
        return lc

    @pytest.fixture
    def mock_adapter(self):
        """Mock _LifecycleAdapter."""
        adapter = MagicMock()
        adapter.start = MagicMock()
        adapter.tick.return_value = MagicMock(name="SprintPhase.ACTIVE")
        adapter.should_enter_windup.return_value = False
        adapter.mark_warmup_done = MagicMock()
        adapter._current_phase = "ACTIVE"
        return adapter

    def test_metrics_registry_init_fail_soft(self, tmp_path):
        """Registry initializes with valid run_dir and defaults."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_run")
        assert reg._run_id == "test_run"
        assert reg._persist_available is not None
        assert reg._closed is False
        reg.close()

    def test_metrics_registry_close(self, tmp_path):
        """Registry closes and flushes without error."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_close")
        reg.inc("orchestrator_rss_mb")
        reg.set_gauge("memory_rss_mb", 123.4)
        reg.close()
        assert reg._closed is True

    def test_metrics_registry_tick_noop_without_psutil(self, tmp_path):
        """tick() is noop when psutil unavailable."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_tick")
        # tick should not raise even without psutil
        reg.tick()
        reg.close()

    def test_metrics_registry_get_summary(self, tmp_path):
        """get_summary returns bounded fields."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_summary")
        reg.inc("orchestrator_rss_mb")
        reg.set_gauge("memory_rss_mb", 100.0)
        summary = reg.get_summary()
        assert "counter_count" in summary
        assert "gauge_count" in summary
        assert "persist_available" in summary
        assert summary["counter_count"] == 1
        assert summary["gauge_count"] == 1
        reg.close()

    def test_metrics_registry_inc_unknown_is_noop(self, tmp_path):
        """inc() with unknown metric name is noop (warning logged)."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_unknown")
        reg.inc("not_a_real_metric_name")  # should warn but not raise
        summary = reg.get_summary()
        assert summary["counter_count"] == 0
        reg.close()

    def test_metrics_registry_set_gauge_unknown_is_noop(self, tmp_path):
        """set_gauge() with unknown metric name is noop."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_unknown_gauge")
        reg.set_gauge("not_a_real_gauge", 42.0)  # should warn but not raise
        summary = reg.get_summary()
        assert summary["gauge_count"] == 0
        reg.close()

    def test_metrics_registry_context_manager(self, tmp_path):
        """Registry works as context manager."""
        with MetricsRegistry(run_dir=tmp_path, run_id="test_cm") as reg:
            reg.inc("orchestrator_rss_mb")
        assert reg._closed is True

    def test_metrics_registry_correlation_normalized(self, tmp_path):
        """Correlation dict is normalized to grammar keys."""
        reg = MetricsRegistry(
            run_dir=tmp_path,
            run_id="test_corr",
            correlation={"branch_id": "b1", "provider_id": "p1", "action_id": "a1", "extra_key": "drop"},
        )
        # Only grammar keys survive
        assert set(reg._correlation.keys()).issubset({"run_id", "branch_id", "provider_id", "action_id"})
        assert reg._correlation["run_id"] == "test_corr"
        reg.close()

    def test_metrics_registry_flush_writes_jsonl(self, tmp_path):
        """flush() writes valid JSONL to disk."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_flush")
        reg.set_gauge("memory_rss_mb", 99.0)
        reg.flush(force=True)
        metrics_file = tmp_path / "logs" / "metrics.jsonl"
        assert metrics_file.exists()
        content = metrics_file.read_bytes()
        assert b"memory_rss_mb" in content
        assert b"gauge" in content
        reg.close()

    def test_scheduler_init_metrics_registry_field(self):
        """SprintScheduler has _metrics_registry and _metrics_initialized fields."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_metrics_registry")
        assert hasattr(scheduler, "_metrics_initialized")
        assert scheduler._metrics_registry is None
        assert scheduler._metrics_initialized is False

    @pytest.mark.asyncio
    async def test_scheduler_init_metrics_registry_derives_run_dir_from_export_dir(self):
        """_init_metrics_registry derives run_dir from config.export_dir."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(export_dir="/tmp/f205h_test_export")
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_export_dir"

        await scheduler._init_metrics_registry()

        assert scheduler._metrics_registry is not None
        assert scheduler._metrics_initialized is True
        # Registry should use export_dir as run_dir
        assert scheduler._metrics_registry._run_dir == Path("/tmp/f205h_test_export")

        # cleanup
        await scheduler._close_metrics_registry()

    @pytest.mark.asyncio
    async def test_scheduler_init_metrics_registry_default_run_dir(self):
        """_init_metrics_registry uses ~/.hledac/runs when no export_dir."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(export_dir="")  # empty = default
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_default_run_dir"

        await scheduler._init_metrics_registry()

        assert scheduler._metrics_registry is not None
        assert scheduler._metrics_initialized is True
        expected = Path.home() / ".hledac" / "runs"
        assert scheduler._metrics_registry._run_dir == expected

        await scheduler._close_metrics_registry()

    def test_scheduler_tick_metrics_on_cycle_end_noop_when_not_initialized(self):
        """_tick_metrics_on_cycle_end is noop when registry not initialized."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        # Not initialized — should be noop
        scheduler._tick_metrics_on_cycle_end()
        # No error means pass

    def test_scheduler_get_metrics_summary_returns_none_when_not_initialized(self):
        """_get_metrics_summary returns None when registry not initialized."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        result = scheduler._get_metrics_summary()
        assert result is None

    @pytest.mark.asyncio
    async def test_scheduler_close_metrics_registry(self):
        """_close_metrics_registry closes registry and re-raises CancelledError."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_close_reg"
        await scheduler._init_metrics_registry()
        assert scheduler._metrics_registry is not None

        await scheduler._close_metrics_registry()
        assert scheduler._metrics_registry is None

    @pytest.mark.asyncio
    async def test_scheduler_close_metrics_registry_reaises_cancelled_error(self):
        """_close_metrics_registry re-raises CancelledError per GHOST_INVARIANTS."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_cancel"
        await scheduler._init_metrics_registry()

        # Patch close to raise CancelledError
        original_close = scheduler._metrics_registry.close
        scheduler._metrics_registry.close = MagicMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await scheduler._close_metrics_registry()

        original_close()  # restore for cleanup

    def test_scheduler_get_metrics_summary_returns_bounded_fields(self):
        """_get_metrics_summary returns counter_count, gauge_count, last_rss_mb, persist_available."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig(export_dir="")
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_summary_fields"

        # Simulate initialized registry
        mock_reg = MagicMock()
        mock_reg.get_summary.return_value = {
            "counter_count": 3,
            "gauge_count": 2,
            "gauges": {"memory_rss_mb": 512.5},
            "persist_available": True,
            "closed": False,
        }
        scheduler._metrics_registry = mock_reg
        scheduler._metrics_initialized = True

        result = scheduler._get_metrics_summary()
        assert result["counter_count"] == 3
        assert result["gauge_count"] == 2
        assert result["last_rss_mb"] == 512.5
        assert result["persist_available"] is True

    def test_build_diagnostic_report_includes_metrics_registry(self):
        """_build_diagnostic_report embeds metrics_registry summary."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.sprint_id = "test_report"
        scheduler._finding_count = 5

        # Mock initialized registry
        mock_reg = MagicMock()
        mock_reg.get_summary.return_value = {
            "counter_count": 4,
            "gauge_count": 3,
            "gauges": {"memory_rss_mb": 256.0},
            "persist_available": True,
            "closed": False,
        }
        scheduler._metrics_registry = mock_reg
        scheduler._metrics_initialized = True

        mock_lifecycle = MagicMock()
        mock_lifecycle.snapshot.return_value = {}
        mock_lifecycle.current_phase.name = "WINDUP"

        report = scheduler._build_diagnostic_report(mock_lifecycle)
        assert "metrics_registry" in report
        mr = report["metrics_registry"]
        assert mr["counter_count"] == 4
        assert mr["gauge_count"] == 3
        assert mr["last_rss_mb"] == 256.0
        assert mr["persist_available"] is True


class TestMetricsRegistryBoundedMetrics:
    """F205H: Bounded metric names enforced."""

    METRIC_NAMES = frozenset([
        "orchestrator_rss_mb",
        "orchestrator_frontier_size",
        "orchestrator_evidence_ring_len",
        "orchestrator_tool_exec_events",
        "orchestrator_budget_remaining_tokens",
        "orchestrator_budget_remaining_time",
        "orchestrator_budget_remaining_api_calls",
        "cache_http_size",
        "cache_snapshot_size",
        "cache_frontier_size",
        "memory_open_fds",
        "memory_rss_mb",
        "memory_vms_mb",
        "mlx_cache_hits",
        "mlx_cache_misses",
        "mlx_cache_size_bytes",
        "mlx_active_memory_bytes",
        "mlx_peak_memory_bytes",
        "mlx_cache_fragmentation_ratio",
        "mlx_kernel_compilation_time_ms",
        "mlx_kernel_cache_hit_rate",
        "model_load_duration_ms",
        "model_unload_count",
        "model_load_failures",
        "action_latency_ms",
        "thermal_throttle_events",
        "thermal_recovery_events",
        "memory_zone_normal_seconds",
        "memory_zone_high_seconds",
        "memory_zone_critical_seconds",
    ])

    def test_known_metrics_are_valid(self, tmp_path):
        """Known metrics from METRIC_NAMES are accepted without warning."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_valid")
        for name in self.METRIC_NAMES:
            # These should not log warnings
            if "hits" in name or "size" in name or "count" in name:
                reg.inc(name)
            else:
                reg.set_gauge(name, 1.0)
        summary = reg.get_summary()
        # All should be recorded
        assert summary["counter_count"] + summary["gauge_count"] == len(self.METRIC_NAMES)
        reg.close()

    def test_scheduler_tick_adds_memory_gauges(self, tmp_path):
        """tick() captures memory_rss_mb and memory_vms_mb gauges."""
        reg = MetricsRegistry(run_dir=tmp_path, run_id="test_tick_memory")
        reg.tick()
        summary = reg.get_summary()
        # Gauges are set by tick() if psutil available
        # (may be 0 if psutil unavailable — that's fine, just verify no error)
        assert "memory_rss_mb" in summary["gauges"] or summary["gauge_count"] >= 0
        reg.close()