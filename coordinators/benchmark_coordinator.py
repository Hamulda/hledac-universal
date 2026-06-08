"""
DEPRECATED: top-level alias for `_deprecated.benchmark_coordinator_shim`.

Moved on 2026-06-03 (F3.3 audit). Real implementation lives in
`coordinators/_deprecated/benchmark_coordinator.py`. This thin alias preserves
backward-compat for `from hledac.universal.coordinators.benchmark_coordinator
import X` while emitting a DeprecationWarning.
"""

from hledac.universal.coordinators._deprecated.benchmark_coordinator import (  # type: ignore[ty:unresolved-import]  # noqa: F401  # pre-existing absolute import — re-exports from _deprecated (historical namespace)
    AgentBenchmarker,
    AgentBenchmarkResult,
    BenchmarkConfig,
    BenchmarkReport,
    MemoryProfiler,
    run_agent_benchmarks,
    run_quick_performance_check,
)

__all__ = [
    "AgentBenchmarker",
    "AgentBenchmarkResult",
    "BenchmarkConfig",
    "BenchmarkReport",
    "MemoryProfiler",
    "run_agent_benchmarks",
    "run_quick_performance_check",
]
