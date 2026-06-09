"""
DEPRECATED compatibility shim for `hledac.universal.coordinators.benchmark_coordinator`.

Moved to `coordinators/_deprecated/` on 2026-06-03 (F3.3 audit). Reason:
- 0 external callers in the active codebase (legacy/autonomous_orchestrator.py
  explicitly comments "# Not used").
- Imports `hledac.models.SearchResult` and `hledac.runtime.unified_orchestrator.AgentProtocol`
  from legacy paths.
- Not wired into SprintScheduler; superseded by `benchmarks/` scripts
  (benchmarks/live_measurement_kpi.py, etc.).

This shim re-exports all public symbols but emits a DeprecationWarning on
first attribute access. The real implementation lives in this same file
(moved verbatim from `coordinators/benchmark_coordinator.py`); future sprints
can delete the entire `_deprecated/` directory once any internal callers are
migrated.
"""

from __future__ import annotations

import warnings

from hledac.universal.coordinators._deprecated import (
    benchmark_coordinator as _real_module,  # type: ignore[ty:unresolved-import]  # pre-existing absolute import — circular-ish self-ref under deprecated shim (historical namespace)
)

__all__ = [
    "AgentBenchmarker",  # noqa: F822
    "AgentBenchmarkResult",  # noqa: F822
    "BenchmarkConfig",  # noqa: F822
    "BenchmarkReport",  # noqa: F822
    "MemoryProfiler",  # noqa: F822
    "run_agent_benchmarks",  # noqa: F822
    "run_quick_performance_check",  # noqa: F822
]

_warned = False


def _warn_once() -> None:
    global _warned
    if not _warned:
        warnings.warn(
            "hledac.universal.coordinators.benchmark_coordinator is deprecated "
            "as of 2026-06-03. Use benchmarks/ scripts (e.g. "
            "benchmarks/live_measurement_kpi.py) directly. This shim will be "
            "removed in a future release.",
            DeprecationWarning,
            stacklevel=3,
        )
        _warned = True


def __getattr__(name: str):
    if name in __all__:
        _warn_once()
        return getattr(_real_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
