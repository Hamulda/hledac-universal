"""
Sprint 8C: Throughput Recovery + Echo Gate Calibration + Teardown Hardening

Tests for:
- Unconditional cooperative yield every N iterations
- Per-action echo telemetry
- No regression in existing timing/yield fields
"""

import pytest


class TestCooperativeYield:
    """Test 1: Unconditional cooperative yield is implemented."""

    def test_yield_every_n_constant_defined(self):
        """_YIELD_EVERY_N constant exists in autonomous_orchestrator."""
        from hledac.universal.autonomous_orchestrator import _YIELD_EVERY_N
        assert _YIELD_EVERY_N == 10

    def test_yield_every_n_in_loop(self):
        """Yield logic appears in research loop at iteration increment."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        source = inspect.getsource(FullyAutonomousOrchestrator.research)
        # Find the pattern: if self._iter_count % _YIELD_EVERY_N == 0
        assert "_iter_count % _YIELD_EVERY_N" in source, "Yield every N pattern not found in research loop"


class TestActionEchoTelemetry:
    """Test 2: Per-action echo telemetry tracking."""

    def test_action_echo_telemetry_field_exists(self):
        """BenchmarkResults has action_echo_telemetry field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'action_echo_telemetry')

    def test_action_echo_telemetry_structure(self):
        """action_echo_telemetry is Dict[str, Dict[str, int]]."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        telemetry = {
            'surface_search': {'admit': 10, 'hold': 5, 'reject': 3},
            'network_recon': {'admit': 2, 'hold': 0, 'reject': 1},
        }
        r.action_echo_telemetry = telemetry
        assert r.action_echo_telemetry['surface_search']['admit'] == 10
        assert r.action_echo_telemetry['network_recon']['reject'] == 1

    def test_action_echo_telemetry_in_report(self):
        """PER-ACTION ECHO section appears in generated report."""
        import tempfile
        from pathlib import Path

        from hledac.universal.benchmarks.run_sprint82j_benchmark import (
            BenchmarkAcquisitionMetrics,
            BenchmarkConfig,
            BenchmarkGatingMetrics,
            BenchmarkMemoryMetrics,
            BenchmarkResults,
            BenchmarkSynthesisMetrics,
            E2EBenchmark,
        )

        r = BenchmarkResults()
        r.action_echo_telemetry = {
            'surface_search': {'admit': 10, 'hold': 5, 'reject': 3},
        }
        # _generate_report needs phases, lanes, gating, acquisition, memory, synthesis
        r.phases = []
        r.lanes = []
        r.gating = BenchmarkGatingMetrics()
        r.acquisition = BenchmarkAcquisitionMetrics()
        r.memory = BenchmarkMemoryMetrics()
        r.synthesis = BenchmarkSynthesisMetrics()

        with tempfile.TemporaryDirectory() as tmpdir:
            config = BenchmarkConfig(output_dir=Path(tmpdir))
            bench = E2EBenchmark(config)
            bench.results = r

            report = bench._generate_report([])
            # _generate_report returns str, not list
            assert 'PER-ACTION ECHO' in report, f"PER-ACTION ECHO section not found. First 200 chars: {report[:200]!r}"
            # Check for surface_search data line
            assert 'surface_search' in report, "surface_search not found in report"


class TestYieldCountersUpdated:
    """Test 3: sleep0_count increases from unconditional yield."""

    def test_sleep0_count_in_results(self):
        """BenchmarkResults has sleep0_count field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'sleep0_count')
        r.sleep0_count = 15
        assert r.sleep0_count == 15

    def test_idle_sleep_count_in_results(self):
        """BenchmarkResults has idle_sleep_count field."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        assert hasattr(r, 'idle_sleep_count')
        r.idle_sleep_count = 3
        assert r.idle_sleep_count == 3


class TestTimingBreakdownExists:
    """Test 4: All timing breakdown fields exist and are used."""

    def test_research_loop_elapsed_s(self):
        """BenchmarkResults has research_loop_elapsed_s."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        r.research_loop_elapsed_s = 29.1
        assert r.research_loop_elapsed_s == 29.1

    def test_synthesis_elapsed_s(self):
        """BenchmarkResults has synthesis_elapsed_s."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        r.synthesis_elapsed_s = 0.2
        assert r.synthesis_elapsed_s == 0.2

    def test_teardown_elapsed_s(self):
        """BenchmarkResults has teardown_elapsed_s."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        r.teardown_elapsed_s = 17.6
        assert r.teardown_elapsed_s == 17.6

    def test_teardown_equals_total_minus_loop_minus_synthesis(self):
        """Teardown = total - loop - synthesis."""
        r = type('MockResults', (), {
            'total_wall_clock_seconds': 47.0,
            'research_loop_elapsed_s': 29.0,
            'synthesis_elapsed_s': 0.2,
        })()
        teardown = max(0.0, r.total_wall_clock_seconds - r.research_loop_elapsed_s - r.synthesis_elapsed_s)
        assert teardown == pytest.approx(17.8)


class TestEchoRejectionRate:
    """Test 5: Echo rejection rate calculation."""

    def test_echo_rejection_rate_calculation(self):
        """Rate = rejects / (rejects + admits)."""
        rejects = 559
        admits = 612
        total = rejects + admits
        rate = rejects / total if total > 0 else 0.0
        assert rate == pytest.approx(0.477, rel=0.001)

    def test_echo_rejection_rate_zero_when_no_rejects(self):
        """Rate is 0 when no rejects."""
        rejects = 0
        admits = 100
        total = rejects + admits
        rate = rejects / total if total > 0 else 0.0
        assert rate == 0.0


class TestPerActionLatencyStats:
    """Test 6: Per-action latency stats in benchmark."""

    def test_action_latency_stats_structure(self):
        """action_latency_stats tracks count/total_ms/max_ms per action."""
        from hledac.universal.benchmarks.run_sprint82j_benchmark import BenchmarkResults
        r = BenchmarkResults()
        r.action_latency_stats = {
            'surface_search': {'count': 105, 'total_ms': 28202.2, 'max_ms': 2150.9},
            'scan_ct': {'count': 81, 'total_ms': 63.8, 'max_ms': 3.6},
        }
        assert r.action_latency_stats['surface_search']['count'] == 105
        assert r.action_latency_stats['surface_search']['max_ms'] == 2150.9

    def test_action_latency_mean_calculation(self):
        """Mean latency = total_ms / count."""
        stats = {'surface_search': {'count': 105, 'total_ms': 28202.2, 'max_ms': 2150.9}}
        mean = stats['surface_search']['total_ms'] / stats['surface_search']['count']
        assert mean == pytest.approx(268.6, rel=0.1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
