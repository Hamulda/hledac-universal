"""
mem_stats — Shared memory stats utilities for M1 8GB UMA benchmarks.

Provides:
    get_rss_mb(): Current process RSS in MB (fail-safe, psutil-based).

M1 8GB-safe: used across all benchmark scripts to track memory ceiling.
Bounded, always-on, no feature flags.
"""

import psutil


def get_rss_mb() -> float:
    """
    Get current process RSS in MB.

    Fail-safe: returns 0.0 if psutil is unavailable.
    Used by all benchmark scripts to track M1 8GB memory ceiling.
    """
    try:
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0
