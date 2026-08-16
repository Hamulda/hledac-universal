"""
Adaptive Scheduler Rust Integration Wiring
========================================

Wires rust_extensions/src/adaptive_scheduler.rs to:
- _core/rust_backend/pools.py
- utils/isolated_executors.py

Purpose:
- MLX memory-aware thread scheduling
- CPU saturation detection
- Workload type classification (CPU/IO/Mixed)

Integration Point:
- Thread pool sizing decisions
- Phase-based thread allocation

Usage:
    from rust_extensions.wiring.adaptive_scheduler_wiring import adaptive_scheduler_wired
    
    threshold = adaptive_scheduler_wired.get_mixed_threshold()
    config = adaptive_scheduler_wired.get_phase_config("ACTIVE")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_adaptive_scheduler

# Create singleton instance
_adaptive_scheduler = get_adaptive_scheduler()


def adaptive_scheduler_wired():
    """Get the wired adaptive scheduler integration."""
    return _adaptive_scheduler


def get_thread_budget() -> dict[str, int]:
    """
    Get current thread budget configuration.

    Returns:
        Dict with max_total, available, dispatchers counts.
    """
    return _adaptive_scheduler.get_thread_budget()


def get_mixed_threshold() -> int:
    """
    Get recommended chunk size for mixed workloads.

    Based on MLX memory pressure:
    - < 0.60 GPU fraction → 16 (idle)
    - 0.60–0.85          → 32 (normal)
    - > 0.85             → 64 (pressure)
    """
    return _adaptive_scheduler.get_mixed_threshold()


def get_phase_config(phase: str) -> dict[str, int]:
    """
    Get thread configuration for a specific phase.

    Args:
        phase: Phase name (BOOT, ACTIVE, DEGRADED, SYNTHESIS, etc.)

    Returns:
        Dict with cpu, io, mixed_max thread counts.
    """
    return _adaptive_scheduler.get_phase_config(phase)


def recommend_pool_size(
    phase: str,
    workload_type: str = "cpu",
) -> int:
    """
    Recommend pool size based on phase and workload type.

    Args:
        phase: Current execution phase
        workload_type: cpu | io | mixed

    Returns:
        Recommended thread count for the pool.
    """
    config = get_phase_config(phase)

    if workload_type == "cpu":
        return config.get("cpu", 2)
    elif workload_type == "io":
        return config.get("io", 1)
    elif workload_type == "mixed":
        return config.get("mixed_max", 1)
    else:
        return 2  # Default


# Check availability at import time for logging
if _adaptive_scheduler.available:
    logger.info("[AdaptiveScheduler] Rust adaptive_scheduler.rs integration: ENABLED")
else:
    logger.info("[AdaptiveScheduler] Rust adaptive_scheduler.rs integration: DISABLED (using Python defaults)")
