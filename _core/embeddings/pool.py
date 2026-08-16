"""
Embedding worker thread pool for MLX — M1 8GB UMA.

M-11 role:
  Canonical MLX model access is via brain._hermes_cache.hermes_cache().
  This module provides the shared embedding worker thread infrastructure.

Key responsibilities:
  - Shared embedding worker thread (via get_mlx_model_worker)
  - get_mlx_model: DEPRECATED — delegates to brain._hermes_cache.hermes_cache()
  - Metal limits + cleanup: DELEGATED to utils.mlx_cache (canonical)

M-11 architecture:
  - utils.mlx_cache:           Metal limits + cleanup (CANONICAL)
  - brain._hermes_cache:       HermesModelCache singleton for LLM inference (SINGLETON ENTRY POINT)
  - core.embeddings.pool:      Embedding worker thread (NOT a model cache)

Key invariants:
- mx.eval([]) before mx.metal.clear_cache() — always
- Fail-safe: MLX unavailable → no-op, never raises
- Dynamic Metal cache: min(max(available*0.2, 512MiB), 1.5GiB)
"""

import asyncio
import gc
import importlib.util
import logging
import threading
import sys as _sys
from typing import Any

from hledac.universal.utils.mlx_memory import (
    get_dynamic_metal_cache_limit,
    mlx_cleanup_aggressive as _canonical_mlx_cleanup_aggressive,
    mlx_cleanup_sync as _canonical_mlx_cleanup_sync,
    mlx_cleanup_decorator as _mlx_cleanup_decorator_canonical,
    )

# === Metal memory management — DELEGATED to utils.mlx_cache (canonical) ===
# All Metal limit management is handled by the canonical implementation in utils.mlx_cache.
# These imports provide backward compatibility for code that imports from here.

from hledac.universal.utils.mlx_cache import (
    get_metal_limits_status as _canonical_get_metal_limits_status,
    reconfigure_metal_cache_limit as _canonical_reconfigure_metal_cache_limit,
    _ensure_metal_memory_limits as _canonical_ensure_metal_memory_limits,
    )

logger = logging.getLogger(__name__)

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
# Uses importlib.metadata.version("mlx") — no mlx.core import at module load
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from _core._util import aclose


def get_mx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    # C1-X FIX: Use centralized get_mx() from mlx_memory SSOT
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    return _get_mx_from_core()


# === Deprecated get_mlx_model — delegates to hermes_cache (M-11) ===
async def get_mlx_model(model_name: str) -> tuple[Any, Any]:
    """
    DEPRECATED — M-11: Use brain._hermes_cache.hermes_cache() instead.

    This function is kept for backward compatibility only.
    Delegates to the HermesModelCache singleton.
    """
    import warnings

    warnings.warn(
        "get_mlx_model() is deprecated — use brain._hermes_cache.hermes_cache() instead. "
        "This function will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Lazy import to avoid circular dependency
    from hledac.universal.brain._hermes_cache import hermes_cache

    cache = hermes_cache()
    result = cache.get_model(model_name)
    if result is not None:
        return result

    # Not cached — load and put
    try:
        from mlx_lm import load as mlx_load

        model, tokenizer, *_ = await asyncio.to_thread(mlx_load, model_name)
        cache.put_model(model_name, model, tokenizer)
        return model, tokenizer
    except Exception as e:
        logger.warning(f"Failed to load MLX model {model_name}: {e}")
        return None, None


# === Metal memory management — DELEGATED to utils.mlx_cache (canonical) ===
# Re-exported for backward compatibility with code that imports from here.

_MLX_INITIALIZED = False


def _format_limit_mib(value: int | None) -> str:
    """Format a memory limit in bytes to MiB string."""
    if value is None:
        return "unavailable"
    return f"{value // 1024 ** 2} MiB"


# Re-export canonical functions for backward compatibility
get_metal_limits_status = _canonical_get_metal_limits_status
reconfigure_metal_cache_limit = _canonical_reconfigure_metal_cache_limit
_ensure_metal_memory_limits = _canonical_ensure_metal_memory_limits


def init_mlx_buffers() -> bool:
    """
    Initialize MLX buffer limits for M1 8GB.

    Sets:
    - cache_limit: dynamic (20% of available, ceiling 1.5 GiB)
    - wired_limit: fixed 768 MiB

    Thread-safe double-checked locking.
    Returns False when MLX unavailable (never raises).
    """
    global _MLX_INITIALIZED
    if not MLX_AVAILABLE:
        return False
    if _MLX_INITIALIZED:
        return True

    _canonical_ensure_metal_memory_limits()
    _MLX_INITIALIZED = True
    status = get_metal_limits_status()
    logger.info(
        f"MLX buffers initialized: cache={_format_limit_mib(status['cache_limit_bytes'])}, "
        f"wired={_format_limit_mib(status['wired_limit_bytes'])}, "
        f"configured={status['configured']}, error={status['last_error']}"
    )
    return True


# === Cleanup — DELEGATED to utils.mlx_memory (canonical) ===
# Re-export from canonical mlx_memory module to maintain API compatibility
# while avoiding code duplication.

mlx_cleanup_sync = _canonical_cleanup_sync
mlx_cleanup_aggressive = _canonical_cleanup_aggressive
mlx_cleanup_decorator = _mlx_cleanup_decorator_canonical  # DEPRECATED: use utils.mlx_memory.mlx_cleanup_decorator instead
