"""
Centralized Memory Allocator Ledger — MODERN-42 Fix.

ROLE: Thread-safe allocation accounting for all subsystems (MLX, DuckDB, Tokio, Kuzu).

This module provides the Python facade to the Rust atomic allocator ledger in
rust_extensions/src/memory.rs. All subsystem allocations must go through
this ledger to ensure accurate total memory tracking.

USAGE:
    from hledac.universal.utils.allocations import acquire, release, get_stats

    # Acquire 1 GiB for MLX inference
    ok = acquire(1.0, "mlx")
    if not ok:
        raise MemoryError("Would exceed allocation ceiling")

    # Release when done
    release(1.0, "mlx")

    # Check current allocation stats
    total, ceiling, utilization = get_stats()

AUTHORITY BOUNDARY:
- ALLOCATOR (this module): request-level budgeting/concurrency
- SAMPLER (utils/uma_budget.py): raw memory sampling, no policy
- GOVERNOR (core/resource_governor.py): policy/hysteresis/runtime governance

Python 3.14+ Compatible: Yes
M1 8GB Optimized: Yes (uses atomic operations via Rust)
"""
from __future__ import annotations

import logging
from enum import IntEnum
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    from hledac.universal.utils.uma_budget import UmaBudget

__all__ = [
    'Subsystem',
    'acquire',
    'release',
    'get_stats',
    'set_ceiling',
    'AllocationError',
]

logger = logging.getLogger(__name__)


class Subsystem(IntEnum):
    """
    Subsystem identifiers for allocation tracking.

    Values match rust_extensions/src/memory.rs::Subsystem enum.
    """
    MLX = 0     # MLX Metal allocations
    DUCKDB = 1  # DuckDB memory-mapped files
    TOKIO = 2   # Tokio task heap allocations
    KUZU = 3    # Kuzu graph database
    OTHER = 4   # Generic/uncategorized

    @classmethod
    def from_str(cls, name: str) -> Subsystem:
        """Create Subsystem from string name (case-insensitive)."""
        name_lower = name.lower()
        mapping = {
            'mlx': cls.MLX,
            'duckdb': cls.DUCKDB,
            'tokio': cls.TOKIO,
            'kuzu': cls.KUZU,
            'other': cls.OTHER,
            # Aliases
            'metal': cls.MLX,
            'mlx_metal': cls.MLX,
        }
        if name_lower in mapping:
            return mapping[name_lower]
        return cls.OTHER


# Module-level reference to Rust memory module (lazy import)
_rust_mem_module = None
_uma_synced = False  # FIX: Track if sync_from_uma_budget has been called


def _get_rust_mem():
    """Lazy import of Rust memory module."""
    global _rust_mem_module, _uma_synced
    if _rust_mem_module is None:
        try:
            from hledac.universal.rust_extensions import memory as _mem
            _rust_mem_module = _mem
            # FIX: Sync from UmaBudget on first Rust module load
            if not _uma_synced:
                sync_from_uma_budget()
                _uma_synced = True
        except ImportError:
            logger.warning(
                "[ALLOC-LEDGER] Rust memory module unavailable — using Python fallback"
    )
            _rust_mem_module = None
    return _rust_mem_module


class AllocationError(Exception):
    """Raised when allocation would exceed ceiling."""
    pass


def acquire(gib: float, subsystem: str | Subsystem) -> bool:
    """
    Acquire memory allocation from the centralized ledger.

    Args:
        gib: Amount to allocate in GiB
        subsystem: Subsystem name ("mlx", "duckdb", "tokio", "kuzu") or Subsystem enum

    Returns:
        True if allocation succeeded, False if it would exceed ceiling.

    Raises:
        AllocationError: If allocation would exceed ceiling (optional, use raises=True)

    Thread-safe via atomic compare-and-swap in Rust.

    Example:
        if not acquire(1.5, "mlx"):
            logger.warning("MLX allocation rejected — memory pressure")
            return False
        try:
            result = mlx_inference(data)
            return result
        finally:
            release(1.5, "mlx")
    """
    if isinstance(subsystem, str):
        subsys_enum = Subsystem.from_str(subsystem)
    else:
        subsys_enum = subsystem

    rust = _get_rust_mem()
    if rust is not None:
        ok, total, ceiling = rust.allocate_bytes(gib, subsys_enum.value)
        if not ok:
            # FIX: total/ceiling are in bytes, convert to GiB for log readability
            logger.warning(
                f"[ALLOC-LEDGER] Allocation rejected: {gib:.2f} GiB for {subsystem} "
                f"would exceed ceiling ({total / (1024**3):.2f} / {ceiling / (1024**3):.2f} GiB)"
    )
        return ok

    # Fallback: Python-only mode (no Rust, accept all allocations)
    # In production, this should be rare — Rust module should be available
    logger.debug(f"[ALLOC-LEDGER] Fallback: accepting {gib:.2f} GiB for {subsystem}")
    return True


def release(gib: float, subsystem: str | Subsystem) -> None:
    """
    Release memory allocation back to the centralized ledger.

    Args:
        gib: Amount to release in GiB
        subsystem: Subsystem name ("mlx", "duckdb", "tokio", "kuzu") or Subsystem enum

    Thread-safe via atomic fetch_sub in Rust.

    Example:
        release(1.5, "mlx")
    """
    if isinstance(subsystem, str):
        subsys_enum = Subsystem.from_str(subsystem)
    else:
        subsys_enum = subsystem

    rust = _get_rust_mem()
    if rust is not None:
        new_total = rust.release_bytes(gib, subsys_enum.value)
        logger.debug(
            f"[ALLOC-LEDGER] Released {gib:.2f} GiB for {subsystem} "
            f"(now at {new_total / (1024**3):.2f} GiB)"
    )


def get_stats() -> tuple[float, float, float]:
    """
    Get current allocation statistics.

    Returns:
        tuple of (total_allocated_gib, ceiling_gib, utilization_pct)

    Example:
        total, ceiling, util = get_stats()
        logger.info(f"Memory ledger: {total:.2f}/{ceiling:.2f} GiB ({util:.1f}%)")
    """
    rust = _get_rust_mem()
    if rust is not None:
        total_bytes, ceiling_bytes, utilization = rust.get_allocation_stats()
        return (
            total_bytes / (1024**3),
            ceiling_bytes / (1024**3),
            utilization,
    )

    # Fallback: return zeros
    return (0.0, 0.0, 0.0)


def set_ceiling(gib: float) -> None:
    """
    Set the allocation ceiling (called at startup from SSOT).

    Args:
        gib: New ceiling in GiB

    Default: 6.0625 GiB (6.25 * 0.97, 3% headroom for OS).

    Note: Normally this is set automatically from UmaBudget at startup.
    """
    rust = _get_rust_mem()
    if rust is not None:
        rust.set_allocation_ceiling(gib)
        logger.info(f"[ALLOC-LEDGER] Ceiling set to {gib:.4f} GiB")


def sync_from_uma_budget() -> None:
    """
    Sync allocation ceiling from UmaBudget SSOT.

    Called at startup to ensure consistency with Python SSOT.

    Derivation:
        ceiling = UmaBudget.UMA_HARD_CEILING_GIB * 0.97
               = 6.25 * 0.97
               = 6.0625 GiB
    """
    from hledac.universal.utils.uma_budget import UmaBudget

    ceiling = round(UmaBudget.UMA_HARD_CEILING_GIB * 0.97, 4)
    set_ceiling(ceiling)
    logger.info(f"[ALLOC-LEDGER] Synced from UmaBudget: ceiling={ceiling:.4f} GiB")
