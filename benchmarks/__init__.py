"""
Sprint 0B Benchmarks
====================

Benchmark utilities and smoke tests for runtime verification.

Contents:
- Benchmark manifest
- Performance smoke tests
"""
from __future__ import annotations



__all__ = ['benchmark_manifest']

BENCHMARK_MANIFEST = {
    "probe": "sprint_0b_runtime",
    "checks": [
        "uvloop_install",
        "flow_trace_default_off",
        "flow_trace_summary_safe",
        "session_factory_singleton",
        "async_session_works",
        "bounded_queue",
        "gather_return_exceptions",
    ],
    "env_vars": {
        "HLEDAC_BENCHMARK": "Set to 1 to run benchmark probe",
        "GHOST_FLOW_TRACE": "Set to 1 to enable flow tracing",
    },
}


def benchmark_manifest() -> dict:
    """Return the benchmark manifest."""
    return BENCHMARK_MANIFEST
