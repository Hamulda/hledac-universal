"""
brain/mlx_interface.py — Sprint G2: Unified MLX Interface
=====================================================


Centralizes all MLX imports and metal memory probing in one place.
Replaces scattered lazy mlx imports in DeepHermes3Engine and other brain components.

Problem: MLX imports are scattered across 17+ locations in DeepHermes3Engine
alone. This makes it hard to:
- Mock MLX in tests
- Track which components use MLX
- Change MLX initialization order

Solution: Single import point with lazy initialization and caching.

Usage:
    from brain.mlx_interface import get_mlx, get_metal, get_mlx_lm

    mx = get_mlx()      # mlx.core singleton
    metal = get_metal()  # mx.metal singleton
    mlx_lm = get_mlx_lm()  # mlx_lm module
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Module-level lazy singletons
# ---------------------------------------------------------------------------

_mlx_core: Any | None = None
_mlx_metal: Any | None = None
_mlx_lm_module: Any | None = None
_mlx_available: bool = False
_init_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def is_mlx_available() -> bool:
    """Check if MLX is available on this system."""
    global _mlx_available
    if _mlx_available:
        return True
    try:
        import mlx.core as mx
        _mlx_available = mx is not None
    except Exception:
        _mlx_available = False
    return _mlx_available


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------


def get_mlx() -> Any:
    """
    Get mlx.core singleton.

    Thread-safe lazy initialization. Once initialized, returns cached instance.
    """
    global _mlx_core
    if _mlx_core is not None:
        return _mlx_core
    with _init_lock:
        if _mlx_core is None:
            try:
                import mlx.core as mx
                _mlx_core = mx
            except Exception as e:
                raise RuntimeError(f"MLX not available: {e}")
    return _mlx_core


def get_metal() -> Any:
    """
    Get mx.metal singleton.

    Returns the Metal backend for GPU memory operations.
    Raises RuntimeError if Metal is not available.
    """
    global _mlx_metal
    if _mlx_metal is not None:
        return _mlx_metal
    with _init_lock:
        if _mlx_metal is None:
            mx = get_mlx()
            if not hasattr(mx, "metal"):
                raise RuntimeError("Metal not available on this system")
            _mlx_metal = mx.metal
    return _mlx_metal


def get_mlx_lm() -> Any:
    """
    Get mlx_lm module singleton.

    Lazy import to avoid loading mlx_lm until actually needed.
    """
    global _mlx_lm_module
    if _mlx_lm_module is not None:
        return _mlx_lm_module
    with _init_lock:
        if _mlx_lm_module is None:
            try:
                import mlx_lm as _mlx_lm
                _mlx_lm_module = _mlx_lm
            except Exception as e:
                raise RuntimeError(f"mlx_lm not available: {e}")
    return _mlx_lm_module


# ---------------------------------------------------------------------------
# Metal memory operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetalMemoryInfo:
    """Snapshot of Metal memory state."""
    active_bytes: int
    active_gib: float
    available: bool


def get_active_memory() -> int:
    """
    Get current Metal active memory in bytes.

    Returns 0 if unavailable.
    """
    try:
        metal = get_metal()
        if hasattr(metal, "get_active_memory"):
            return int(metal.get_active_memory())
        mx = get_mlx()
        if hasattr(mx, "get_active_memory"):
            return int(mx.get_active_memory())
    except Exception:  # noqa: BLE001
        pass
    return 0


def get_memory_info() -> MetalMemoryInfo:
    """
    Get Metal memory snapshot.

    Returns MetalMemoryInfo with active_bytes, active_gib, and available flag.
    """
    try:
        active = get_active_memory()
        return MetalMemoryInfo(
            active_bytes=active,
            active_gib=active / (1024**3),
            available=True,
        )
    except Exception:
        return MetalMemoryInfo(active_bytes=0, active_gib=0.0, available=False)


def metal_clear_cache() -> None:
    """
    Clear Metal memory allocator cache.

    Must be called from a thread with valid Metal context.
    Safe: no-op if Metal is unavailable.
    """
    try:
        import mlx.core as mx
        mx.eval([])
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Module reset (for testing)
# ---------------------------------------------------------------------------


def reset_mlx_interface() -> None:
    """Reset all singletons (for testing only)."""
    global _mlx_core, _mlx_metal, _mlx_lm_module, _mlx_available
    with _init_lock:
        _mlx_core = None
        _mlx_metal = None
        _mlx_lm_module = None
        _mlx_available = False
