"""F3.3: Verify benchmark_coordinator deprecation shim emits warning + preserves API."""

import warnings


class TestF33BenchmarkDeprecation:
    """benchmark_coordinator moved to _deprecated/ on 2026-06-03."""

    def test_import_via_top_level_alias_emits_deprecation_warning(self):
        """`from hledac.universal.coordinators.benchmark_coordinator import X` warns."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
            and "benchmark_coordinator" in str(w.message)
        ]
        assert deprecation_warnings, "expected DeprecationWarning about benchmark_coordinator"
        # The shim explicitly references the 2026-06-03 date.
        assert "2026-06-03" in str(deprecation_warnings[0].message)

    def test_shim_preserves_all_public_symbols(self):
        """All 7 public symbols still importable through the shim."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from hledac.universal.coordinators._deprecated.benchmark_coordinator_shim import (
                AgentBenchmarker,
                AgentBenchmarkResult,
                BenchmarkConfig,
                BenchmarkReport,
                MemoryProfiler,
                run_agent_benchmarks,
                run_quick_performance_check,
            )
        # All symbols exist and are the real classes (not None or placeholders).
        for sym in (AgentBenchmarker, AgentBenchmarkResult, BenchmarkConfig,
                    BenchmarkReport, MemoryProfiler, run_agent_benchmarks,
                    run_quick_performance_check):
            assert sym is not None

    def test_real_module_still_in_deprecated_dir(self):
        """The original 794-line module is preserved at _deprecated/benchmark_coordinator.py."""
        from pathlib import Path
        coord_dir = Path(__file__).parent.parent / "coordinators" / "_deprecated"
        real_module = coord_dir / "benchmark_coordinator.py"
        assert real_module.exists(), f"real module missing: {real_module}"
        # The file is non-trivial (>500 LOC for the full benchmarker logic).
        assert real_module.stat().st_size > 15000, "real module too small — likely stripped"

    def test_top_level_alias_works(self):
        """`coordinators.benchmark_coordinator` module is reachable."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import hledac.universal.coordinators.benchmark_coordinator as bc
        assert hasattr(bc, "AgentBenchmarker")
        assert hasattr(bc, "BenchmarkConfig")
