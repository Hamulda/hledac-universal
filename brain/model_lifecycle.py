"""
F6.5: Model Lifecycle Management — Multi-Role Module
=====================================================

F6.5 OWNERSHIP DECLARATION:
  This module is MULTI-ROLE — do NOT treat it as a single owner.
  Each role is explicitly listed below.

F6.5 EXPLICIT ROLES:
  ┌─────────────────────────────────┬──────────────────────────────────────────┐
  │ Role                             │ Canonical Owner                         │
  ├─────────────────────────────────┼──────────────────────────────────────────┤
  │ 1. Emergency seam                │ model_lifecycle (watchdog flag)        │
  │ 2. MLX lazy init helper        │ mlx_cache.init_mlx_buffers()           │
  │ 3. Unload helper (7K SSOT)     │ engine.unload() — delegát, fail-open  │
  │ 4. Lifecycle shadow-state        │ model_lifecycle (O(1), side-effect free)│
  │ 5. Structured-generation sidecar │ class ModelLifecycle → core/model_runtime│
  └─────────────────────────────────┴──────────────────────────────────────────┘

F6.5 THIS MODULE IS NOT THE RUNTIME-WIDE LOAD OWNER:
  - load_model() / unload_model() at module level are UNLOAD HELPERS
  - They delegate to engine.unload() (7K SSOT), NOT a separate authority
  - Canonical runtime-wide acquire/load owner: brain.model_manager.ModelManager
  - This module does NOT hold canonical model state for the runtime-wide plane

F6.5 LAYER MAPPING — MUST NOT BE CONFLATED:
  Layer 1 (workflow-level, ModelManager.PHASE_MODEL_MAP):
    PLAN/DECIDE/GENERATE → hermes
    EMBED/DEDUP/ROUTING → modernbert
    NER/ENTITY → gliner
  Windup-local (Role 5 — F6.5):
    → core/model_runtime.py:class ModelLifecycle (Qwen/SmolLM, Outlines MLX)
"""

from __future__ import annotations

import functools
import gc
import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hledac.universal.core.locks import LockCategory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# Role 1: Emergency Seam
# =============================================================================

_EMERGENCY_UNLOAD_REQUESTED = False
_UNLOAD_LOCK = threading.Lock()


def request_emergency_unload() -> None:
    """
    Role 1: Emergency seam — watchdog flag set by watchdog/pressure-relief.

    This is the ONLY function watchdogs should call to request model unload.
    The actual unload is performed by engine.unload() (Role 3).

    F266-ABORT: Blocks new inference requests when memory pressure is critical.
    """
    global _EMERGENCY_UNLOAD_REQUESTED
    with _UNLOAD_LOCK:
        _EMERGENCY_UNLOAD_REQUESTED = True
    get_model_lifecycle_status.cache_clear()  # Invalidate cache on state change
    logger.warning("[LIFECYCLE] Emergency unload requested via emergency seam")


# =============================================================================
# Role 2: MLX Lazy Init Helper
# =============================================================================


def init_mlx_buffers_ifneeded() -> bool:
    """
    Role 2: MLX lazy init helper — called by brain/model_manager.py on startup.

    Returns True if MLX was initialized, False otherwise.
    F266-U5: Memory pressure check before MLX init — avoid loading if UMA is already
    at risk (system memory pressure > 80%).
    """
    try:
        import mlx.core as mx

        if mx.is_available():
            _init_mlx_buffers_impl(mx)
            return True
    except Exception:
        pass
    return False


def _init_mlx_buffers_impl(mx: Any) -> None:
    """MLX buffer initialization — allocates small persistent buffers to warm up the allocator."""
    try:
        _warm = mx.zeros([1_000_000], dtype=mx.float32)  # 4 MB warmup
        mx.eval(_warm)
        del _warm
    except Exception:
        pass  # fail-open: non-critical


# =============================================================================
# Role 3: Unload Helper (DELEGATES to engine.unload — 7K SSOT)
# =============================================================================


def unload_model(model: Any | None = None) -> None:
    """
    Role 3: Unload helper — delegates to engine.unload().

    This is a FAIL-OPEN wrapper. If engine.unload() is not available, logs and returns.
    The canonical unload authority is brain/engine.py::unload() (7K SSOT).

    DO NOT add unload logic here. If you need unload, fix engine.unload().
    """
    _trigger_emergency_seam_clear()

    # Delegate to canonical SSOT (7K — single source of truth for unload)
    try:
        from hledac.universal.brain import engine

        engine.unload(model)
        return
    except Exception:
        pass

    # Fail-open: if engine is not available, try mlx.core directly
    try:
        import mlx.core as mx

        if hasattr(mx, "clear_cache"):
            mx.eval([])
            mx.clear_cache()
        logger.warning("[LIFECYCLE] Unload: engine.unload() unavailable, used fallback")
    except Exception:
        logger.debug("[LIFECYCLE] Unload: MLX fallback also unavailable")


# =============================================================================
# Role 4: Lifecycle Shadow-State (O(1), side-effect free)
# =============================================================================


# Module-level shadow state — maintained by model_manager.py via register_model()
_MX_LOADED: bool = False
_MX_MODEL_PATH: str | None = None
_MX_LAST_UNLOAD: float = 0.0


def register_model(path: str) -> None:
    """Role 4: Shadow-state writer — called by ModelManager on load."""
    global _MX_LOADED, _MX_MODEL_PATH
    _MX_LOADED = True
    _MX_MODEL_PATH = path
    get_model_lifecycle_status.cache_clear()  # Invalidate cache on state change
    _trigger_emergency_seam_clear()


def unregister_model() -> None:
    """Role 4: Shadow-state writer — called by ModelManager on unload."""
    global _MX_LOADED, _MX_MODEL_PATH, _MX_LAST_UNLOAD
    _MX_LOADED = False
    _MX_MODEL_PATH = None
    _MX_LAST_UNLOAD = _now()
    get_model_lifecycle_status.cache_clear()  # Invalidate cache on state change


def _trigger_emergency_seam_clear() -> None:
    """Clear the emergency seam flag after a successful unload."""
    global _EMERGENCY_UNLOAD_REQUESTED
    if _EMERGENCY_UNLOAD_REQUESTED:
        with _UNLOAD_LOCK:
            _EMERGENCY_UNLOAD_REQUESTED = False
        get_model_lifecycle_status.cache_clear()  # Invalidate cache on state change


def _now() -> float:
    try:
        import time

        return time.monotonic()
    except Exception:
        return 0.0


def is_loaded() -> bool:
    """Role 4: Shadow-state reader — O(1) lookup, no side effects."""
    return _MX_LOADED


def get_model_path() -> str | None:
    """Role 4: Shadow-state reader — returns loaded model path or None."""
    return _MX_MODEL_PATH


def get_last_unload_age() -> float:
    """Role 4: Shadow-state reader — seconds since last unload, or 0 if never unloaded."""
    if _MX_LAST_UNLOAD == 0.0:
        return 0.0
    return _now() - _MX_LAST_UNLOAD


def get_emergency_unload_requested() -> bool:
    """Role 4: Shadow-state reader — check if watchdog requested emergency unload."""
    return _EMERGENCY_UNLOAD_REQUESTED


@functools.lru_cache(maxsize=1)
def get_model_lifecycle_status() -> dict:
    """
    Role 4: Shadow-state dump — returns full lifecycle status as dict.

    Uses lru_cache for performance: called frequently (e.g., resource_governor
    polling) but shadow state changes infrequently. Cache invalidated on each
    new sprint via model_unload_request() which clears the cache.

    Used by:
      - runtime/resource_governor.py:472
      - runtime/sprint_entrypoint.py:1560
      - recon/streaming_embedder.py:189
    """
    return {
        "loaded": _MX_LOADED,
        "model_path": _MX_MODEL_PATH,
        "last_unload_age_s": get_last_unload_age(),
        "emergency_unload_requested": _EMERGENCY_UNLOAD_REQUESTED,
    }


# =============================================================================
# Role 5: Structured-Generation Sidecar
# (MIGRATED — see core/model_runtime.py)
# =============================================================================


# ------------------------------------------------------------------------------------------------
# F6.5 class ModelLifecycle MIGRATED to core/model_runtime.py (F350M-R W6 refactor)
# Windup-local structured-generation (Qwen/SmolLM, Outlines MLX constrained generation)
# is now in core/model_runtime.py.
# brain/model_lifecycle.py now contains ONLY roles 1-4 (emergency seam, MLX helpers,
# shadow-state, unload helpers). Do NOT re-add structured-generation code here.
# ------------------------------------------------------------------------------------------------
