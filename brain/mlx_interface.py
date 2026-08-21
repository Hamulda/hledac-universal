"""
brain/mlx_interface.py — Sprint G2: Unified MLX Interface
=====================================================


Centralizes all MLX imports and metal memory probing in one place.
Replaces scattered lazy mlx imports in DeepHermes3Engine and other brain components.

[SAFE-3] FFI Circuit Breaker integration for MLX inference module.

Problem: MLX imports are scattered across 17+ locations in DeepHermes3Engine
alone. This makes it hard to:
- Mock MLX in tests
- Track which components use MLX
- Change MLX initialization order

Solution: Single import point with lazy initialization and caching.

Usage:
    from hledac.universal.brain.mlx_interface import get_mlx, get_metal, get_mlx_lm

    mx = get_mlx()      # mlx.core singleton
    metal = get_metal()  # mx.metal singleton
    mlx_lm = get_mlx_lm()  # mlx_lm module

NOTE (MODERN-35): Callers must set P-core affinity before mlx_lm.generate().
See brain/deephermes3_engine.py for proper implementation pattern.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from _core.lock_registry import LockCategory, register_lock

_MLXModuleType = Any
from hledac.universal.utils.cpu_affinity import is_apple_silicon, set_mlx_affinity

logger = logging.getLogger(__name__)
try:
    from hledac.universal._core.ffi_circuit_breaker import FFI_MODULE_MLX_INFERENCE, get_ffi_circuit_breaker

    _FFI_CB_AVAILABLE = True
except ImportError:
    _FFI_CB_AVAILABLE = False
    FFI_MODULE_MLX_INFERENCE = "mlx_inference"
__all__ = [
    "get_mlx",
    "get_metal",
    "get_mlx_lm",
    "is_mlx_available",
    "MetalMemoryInfo",
    "get_active_memory",
    "get_memory_info",
    "metal_clear_cache",
    "generate_with_circuit_breaker",
    "embed_with_circuit_breaker",
    "reset_mlx_interface",
]
_mlx_core: _MLXModuleType | None = None
_mlx_metal: _MLXModuleType | None = None
_mlx_lm_module: _MLXModuleType | None = None
_ffi_cb: _MLXModuleType | None = None


@register_lock(LockCategory.MPC)
def _init_lock() -> threading.Lock:
    """Module-level lock for MLX interface lazy singletons."""
    return threading.Lock()


from hledac.universal.utils.mlx_memory import MLX_AVAILABLE


def is_mlx_available() -> bool:
    """Check if MLX is available on this system."""
    return MLX_AVAILABLE


def _get_ffi_cb() -> _MLXModuleType | None:
    """[SAFE-3] Get FFI circuit breaker singleton (lazy init)."""
    global _ffi_cb
    if _ffi_cb is None and _FFI_CB_AVAILABLE:
        try:
            _ffi_cb = get_ffi_circuit_breaker()
        except Exception as e:
            logger.debug("[SAFE-3] FFI circuit breaker unavailable: %s", e)
    return _ffi_cb


def get_mlx() -> _MLXModuleType:
    """
    Get mlx.core singleton.

    Thread-safe lazy initialization. Once initialized, returns cached instance.
    """
    global _mlx_core
    if _mlx_core is not None:
        return _mlx_core
    with _init_lock():
        if _mlx_core is None:
            try:
                import mlx.core as mx

                _mlx_core = mx
            except Exception as e:
                raise RuntimeError(f"MLX not available: {e}")
    return _mlx_core


def generate_with_circuit_breaker(prompt: str, max_tokens: int = 256, temperature: float = 0.7) -> str:
    """
    [SAFE-3] Generate text using MLX with circuit breaker fallback.

    Falls back to empty string if MLX is unavailable or fails.
    """
    ffi_cb = _get_ffi_cb()
    if ffi_cb is None:
        return _generate_mlx(prompt, max_tokens, temperature)

    def rust_call() -> str:
        return _generate_mlx(prompt, max_tokens, temperature)

    result = ffi_cb.call_or_fallback(FFI_MODULE_MLX_INFERENCE, rust_call, prompt, max_tokens, temperature)
    if result.success:
        return result.value
    return ""


def _generate_mlx(prompt: str, max_tokens: int = 256, temperature: float = 0.7) -> str:
    """
    [SAFE-3] Internal MLX generation without circuit breaker.

    Returns empty string if MLX is unavailable.
    """
    if not is_mlx_available():
        logger.warning("[SAFE-3] MLX unavailable for generation")
        return ""
    try:
        mlx_lm = get_mlx_lm()
        if hasattr(mlx_lm, "generate"):
            if is_apple_silicon():
                set_mlx_affinity()
            return mlx_lm.generate(prompt, max_tokens=max_tokens, temp=temperature)
    except Exception as e:
        logger.warning(f"[SAFE-3] MLX generation failed: {e}")
    return ""


def embed_with_circuit_breaker(text: str) -> list[float]:
    """
    [SAFE-3] Generate embeddings using MLX with circuit breaker fallback.

    Falls back to zero vector if MLX is unavailable or fails.
    """
    ffi_cb = _get_ffi_cb()
    if ffi_cb is None:
        return _embed_mlx(text)

    def rust_call() -> list[float]:
        return _embed_mlx(text)

    result = ffi_cb.call_or_fallback(FFI_MODULE_MLX_INFERENCE, rust_call, text)
    if result.success:
        return result.value
    return [0.0] * 256


def _embed_mlx(text: str) -> list[float]:
    """
    [SAFE-3] Internal MLX embedding without circuit breaker.

    Returns zero vector if MLX is unavailable.
    """
    if not is_mlx_available():
        logger.warning("[SAFE-3] MLX unavailable for embedding")
        return [0.0] * 256
    try:
        mlx_lm = get_mlx_lm()
        if hasattr(mlx_lm, "embed"):
            embeddings = mlx_lm.embed(text)
            if hasattr(embeddings, "tolist"):
                return embeddings.tolist()
            return list(embeddings)
    except Exception as e:
        logger.warning(f"[SAFE-3] MLX embedding failed: {e}")
    return [0.0] * 256


def get_metal() -> _MLXModuleType:
    """
    Get mx.metal singleton.

    Returns the Metal backend for GPU memory operations.
    Raises RuntimeError if Metal is not available.
    """
    global _mlx_metal
    if _mlx_metal is not None:
        return _mlx_metal
    with _init_lock():
        if _mlx_metal is None:
            mx = get_mlx()
            if not hasattr(mx, "metal"):
                raise RuntimeError("Metal not available on this system")
            _mlx_metal = mx.metal
    return _mlx_metal


def get_mlx_lm() -> _MLXModuleType:
    """
    Get mlx_lm module singleton.

    Lazy import to avoid loading mlx_lm until actually needed.
    """
    global _mlx_lm_module
    if _mlx_lm_module is not None:
        return _mlx_lm_module
    with _init_lock():
        if _mlx_lm_module is None:
            try:
                import mlx_lm as _mlx_lm

                _mlx_lm_module = _mlx_lm
            except Exception as e:
                raise RuntimeError(f"mlx_lm not available: {e}")
    return _mlx_lm_module


@dataclass(frozen=True, slots=True)
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
    except Exception:
        pass
    return 0


def get_memory_info() -> MetalMemoryInfo:
    """
    Get Metal memory snapshot.

    Returns MetalMemoryInfo with active_bytes, active_gib, and available flag.
    """
    try:
        active = get_active_memory()
        return MetalMemoryInfo(active_bytes=active, active_gib=active / 1024**3, available=True)
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
    except Exception:
        pass


def reset_mlx_interface() -> None:
    """Reset all singletons (for testing only)."""
    global _mlx_core, _mlx_metal, _mlx_lm_module
    with _init_lock():
        _mlx_core = None
        _mlx_metal = None
        _mlx_lm_module = None
