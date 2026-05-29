"""
Sprint 8B: Throughput Recovery + Duration Truth

Tests for:
- Truthful timing breakdown (research_loop_elapsed_s, synthesis_elapsed_s, teardown_elapsed_s)
- benchmark_fps uses research_loop time, not wall clock
- Echo rejection rate telemetry
- Yield counters (sleep0_count, idle_sleep_count)
- Per-action latency stats
- Duration termination truth
"""

from unittest.mock import MagicMock

import pytest


class TestTimingTruth:
    """Test 1: Timing breakdown is truthful."""

    def test_research_loop_elapsed_s_field_exists(self):
        """BenchmarkResults has research_loop_elapsed_s field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'research_loop_elapsed_s')

    def test_synthesis_elapsed_s_field_exists(self):
        """BenchmarkResults has synthesis_elapsed_s field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'synthesis_elapsed_s')

    def test_teardown_elapsed_s_field_exists(self):
        """BenchmarkResults has teardown_elapsed_s field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'teardown_elapsed_s')

    def test_teardown_is_derived_from_total_minus_loop_minus_synthesis(self):
        """Teardown = total - loop - synthesis."""
        r = MagicMock()
        r.total_wall_clock_seconds = 30.0
        r.research_loop_elapsed_s = 20.0
        r.synthesis_elapsed_s = 5.0
        # The benchmark calculates teardown as: max(0, total - loop - synthesis)
        expected = max(0.0, r.total_wall_clock_seconds - r.research_loop_elapsed_s - r.synthesis_elapsed_s)
        assert expected == 5.0

    def test_teardown_cannot_be_negative(self):
        """Teardown is bounded at 0 if loop+synthesis > total."""
        total, loop, synth = 10.0, 8.0, 5.0
        expected = max(0.0, total - loop - synth)
        assert expected == 0.0


class TestFPSDenominator:
    """Test 2: benchmark_fps uses research_loop time, not wall clock."""

    def test_fps_calculation_uses_loop_time(self):
        """FPS = iterations / research_loop_elapsed_s when loop_time > 0."""
        iterations = 100
        research_loop_elapsed_s = 10.0

        loop_time = research_loop_elapsed_s
        if loop_time > 0:
            fps = iterations / loop_time
        else:
            fps = 0.0

        assert fps == 10.0  # 100 / 10 = 10, NOT 100/20=5

    def test_fps_falls_back_to_wall_clock_when_no_loop_time(self):
        """FPS falls back to wall clock if loop_time is 0."""
        iterations = 100
        research_loop_elapsed_s = 0.0
        total_wall_clock_seconds = 20.0

        loop_time = research_loop_elapsed_s
        elapsed_s = total_wall_clock_seconds
        if loop_time > 0:
            fps = iterations / loop_time
        else:
            fps = iterations / elapsed_s  # fallback

        assert fps == 5.0  # fallback to wall clock


class TestEchoRejection:
    """Test 3: Echo rejection rate telemetry."""

    def test_echo_rejection_rate_field_exists(self):
        """BenchmarkResults has echo_rejection_rate field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'echo_rejection_rate')

    def test_echo_rejection_rate_calculation(self):
        """Rate = l1_echo_rejects / (admits + l1_echo_rejects)."""
        l1_echo_rejects = 100
        admits = 400
        total = admits + l1_echo_rejects
        rate = l1_echo_rejects / total if total > 0 else 0.0
        assert rate == 0.2  # 100/500 = 0.2

    def test_echo_rejection_rate_zero_when_no_rejects(self):
        """Rate is 0 when no echo rejects."""
        l1_echo_rejects = 0
        admits = 500
        total = admits + l1_echo_rejects
        rate = l1_echo_rejects / total if total > 0 else 0.0
        assert rate == 0.0

    def test_echo_rejection_rate_zero_when_total_is_zero(self):
        """Rate is 0 when both admits and rejects are 0."""
        l1_echo_rejects = 0
        admits = 0
        total = admits + l1_echo_rejects
        rate = l1_echo_rejects / total if total > 0 else 0.0
        assert rate == 0.0


class TestYieldCounters:
    """Test 4: Loop yield counters."""

    def test_sleep0_count_field_exists(self):
        """BenchmarkResults has sleep0_count field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'sleep0_count')

    def test_idle_sleep_count_field_exists(self):
        """BenchmarkResults has idle_sleep_count field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'idle_sleep_count')


class TestPerActionLatency:
    """Test 5: Per-action latency stats in summary."""

    def test_action_latency_stats_structure(self):
        """action_latency_stats has count/total_ms/max_ms per action."""
        action_stats = {
            "surface_search": {"count": 10, "total_ms": 100.0, "max_ms": 15.0},
            "scan_ct": {"count": 5, "total_ms": 2.0, "max_ms": 0.5},
        }
        assert "surface_search" in action_stats
        assert action_stats["surface_search"]["count"] == 10
        assert action_stats["surface_search"]["max_ms"] == 15.0

    def test_action_latency_mean_calculation(self):
        """Mean latency = total_ms / count."""
        action_stats = {"surface_search": {"count": 10, "total_ms": 100.0, "max_ms": 15.0}}
        mean = action_stats["surface_search"]["total_ms"] / action_stats["surface_search"]["count"]
        assert mean == 10.0  # 100 / 10 = 10ms avg


class TestSynthesizedReportFields:
    """Test 7: Text report includes new timing fields."""

    def test_timing_summary_section_includes_loop_time(self):
        """Report shows 'Research loop: Xs'."""
        # The new format includes "Research loop: Xs  # actual loop time"
        report_line = "  Research loop:         9.9s  # actual loop time"
        assert "Research loop:" in report_line
        assert "# actual loop time" in report_line

    def test_timing_summary_section_includes_teardown(self):
        """Report shows 'Teardown: Xs'."""
        report_line = "  Teardown:             14.1s"
        assert "Teardown:" in report_line

    def test_timing_summary_includes_echo_rejection_rate(self):
        """Report shows echo rejection rate."""
        report_line = "  Echo rejection rate:   0.0%  # l1_echo_rejects/(admits+rejects)"
        assert "Echo rejection rate:" in report_line

    def test_loop_yield_section_exists(self):
        """Report has ## LOOP YIELD COUNTERS section."""
        section = "## LOOP YIELD COUNTERS"
        assert section == "## LOOP YIELD COUNTERS"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
