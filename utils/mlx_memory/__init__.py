"""
utils/mlx_memory — Unified MLX Memory Runtime (F330-MLX-DUP-007, A5-04)

Jediný authoritative modul pro veškerou MLX memory management na M1 8GB.
Ostatní MLX utility moduly jsou deprecated a přesměrovány sem.

A5-04 CONSOLIDATION (2026-07-30)
=================================
Tento modul vs. ostatní paměťové moduly:

| Modul                        | Zodpovědnost                        |
|------------------------------|--------------------------------------|
| core.memory                  | System-wide: RSS, dostupná RAM,     |
|                              | pressure level (Rust SSOT)           |
| core.rust_backend.memory     | DuckDB bridge: domain factory        |
| utils.mlx_memory._core       | MLX-specific: active_mb, peak_mb,   |
|                              | cache_mb, pressure_pct (Metal API)  |

MLX METRICS (v tomto modulu):
    get_mlx_memory_metrics()  — dict: active_mb, peak_mb, cache_mb,
                                pressure_pct, pressure_level
    get_mlx_memory_pressure() — tuple: (pressure_pct, "NORMAL|WARNING|CRITICAL")

SYSTEM METRICS (core.memory):
    get_memory_snapshot() — dict: rss_bytes, available_memory_gib,
                              total_memory_gib, pressure_level

Struktura:
    _core   — canonical MLX runtime (Metal cache, wired limit, model cache, cleanup)
    _slab   — Metal slab allocator pro buffer reuse
    _prompt — MLX prompt KV cache s LRU
    _embedder — Metal pre-allocated buffers pro embedding inference
    _tensor — SharedTensor zero-copy wrapper

M1 8GB budget (suma = 6.25 GiB):
    macOS baseline:   ~2.5 GiB
    Orchestrátor:     ~1.0 GiB
    LLM (Hermes-3):  ~2.0 GiB
    KV cache:        ~0.75 GiB
    Metal cache:      ~0.5–1.1 GiB  (dynamic ceiling 1.5 GiB)
    Metal wired:      768 MiB       (fixed, cannot be swapped)

Usage:
    from hledac.universal.utils.mlx_memory import (
        # Core runtime
        MLX_AVAILABLE,
        init_mlx_buffers,
        configure_mlx_limits,
        clear_mlx_cache,
        get_mlx_memory_metrics,
        get_mlx_memory_pressure,
        # Model cache
        get_mlx_model,
        evict_all,
        get_cache_stats,
        # Cleanup
        mlx_cleanup_sync,
        mlx_cleanup_aggressive,
        # Memory metrics
        get_dynamic_metal_cache_limit,
        get_metal_limits_status,
        # Stream context
        get_metal_stream_context,
        # Slab allocator
        MetalSlabPool,
        # Prompt cache
        MLXPromptCache,
        # Embedder buffers
        MetalBufferPool,
        get_buffer_pool,
        init_metal_embedder_buffers,
        release_metal_embedder_buffers,
        # Shared tensor
        SharedTensor,
    )

Deprecated wrappers (do not import directly):
    mlx_memory    → mlx_memory  (deprecated since F266-U3)
    mlx_lazy      → mlx_memory  (deprecated since F330)
    mlx_utils     → mlx_memory  (deprecated since F330)
    metal_slab_pool → mlx_memory  (deprecated since F330)
    mlx_prompt_cache → mlx_memory  (deprecated since F330)
    shared_tensor → mlx_memory  (deprecated since F330)
    metal_embedder_buffers → mlx_memory  (deprecated since F330)

GHOST_INVARIANT: M1 Metal cache limit 1.5 GiB ceiling na 8GB machines.
Canonical teardown: mx.eval([]) → gc.collect() → mx.clear_cache() → gc.collect()
"""

# ── Re-export canonical API from _core ────────────────────────────────────────

from . import _core as _core_module

# MLX availability
MLX_AVAILABLE = _core_module.MLX_AVAILABLE

# Initialization
init_mlx_buffers = _core_module.init_mlx_buffers
configure_mlx_limits = _core_module.configure_mlx_limits

# Lazy module accessor (centralized, replaces per-class _get_mlx_memory patterns)
get_mlx_memory_module = _core_module.get_mlx_memory_module

# Memory metrics
get_mlx_active_memory_mb = _core_module.get_mlx_active_memory_mb
get_mlx_peak_memory_mb = _core_module.get_mlx_peak_memory_mb
get_mlx_cache_memory_mb = _core_module.get_mlx_cache_memory_mb
get_mlx_memory_pressure = _core_module.get_mlx_memory_pressure
get_mlx_memory_metrics = _core_module.get_mlx_memory_metrics
format_mlx_memory_snapshot = _core_module.format_mlx_memory_snapshot

# Cleanup
clear_mlx_cache = _core_module.clear_mlx_cache
clear_mlx_cache_debounced = _core_module.clear_mlx_cache_debounced
set_cache_limit_with_debounce = _core_module.set_cache_limit_with_debounce
mlx_cleanup_sync = _core_module.mlx_cleanup_sync
mlx_cleanup_aggressive = _core_module.mlx_cleanup_aggressive
metal_reclaim = _core_module.metal_reclaim  # M5: canonical gc+eval+clear+dynamic_limit entry point
safe_clear_metal_cache = _core_module.safe_clear_metal_cache

# Metal limits
get_dynamic_metal_cache_limit = _core_module.get_dynamic_metal_cache_limit
get_metal_limits_status = _core_module.get_metal_limits_status
safe_set_cache_limit = _core_module.safe_set_cache_limit
safe_get_cache_limit = _core_module.safe_get_cache_limit

# Stream context
get_metal_stream_context = _core_module.get_metal_stream_context

# Model cache
get_mlx_model = _core_module.get_mlx_model
evict_all = _core_module.evict_all
get_cache_stats = _core_module.get_cache_stats
get_semaphore = _core_module.get_semaphore

# ── Re-export slab allocator from _slab ──────────────────────────────────────

from . import _slab as _slab_module

MetalSlabPool = _slab_module.MetalSlabPool
release_slab_pool = _slab_module.release_slab_pool

# ── Re-export prompt cache from _prompt ───────────────────────────────────────

from . import _prompt as _prompt_module

MLXPromptCache = _prompt_module.MLXPromptCache

# ── Re-export embedder buffers from _embedder ─────────────────────────────────

from . import _embedder as _embedder_module

MetalBufferPool = _embedder_module.MetalBufferPool
get_buffer_pool = _embedder_module.get_buffer_pool
init_metal_embedder_buffers = _embedder_module.init_metal_embedder_buffers
release_metal_embedder_buffers = _embedder_module.release_metal_embedder_buffers

# ── Re-export shared tensor from _tensor ──────────────────────────────────────

from . import _tensor as _tensor_module

SharedTensor = _tensor_module.SharedTensor

# ── Re-export mlx_utils decorators from _core ─────────────────────────────────

from . import _core as _core_mlx_utils

mlx_managed = _core_mlx_utils.mlx_managed
mlx_cleanup_after = _core_mlx_utils.mlx_cleanup_after
mlx_cleanup_decorator = _core_mlx_utils.mlx_cleanup_after  # Alias for backward compatibility
get_mlx_memory_stats = _core_mlx_utils.get_mlx_memory_stats
reset_metal_peak = _core_mlx_utils.reset_metal_peak

# ── Public aliases (test surface) ─────────────────────────────────────────────

_MLX_CACHE_LIMIT = _core_module._METAL_CACHE_LIMIT_BYTES
_MLX_WIRED_LIMIT = _core_module._METAL_WIRED_LIMIT_BYTES

__all__ = [
    # Availability
    "MLX_AVAILABLE",
    # Init
    "init_mlx_buffers",
    "configure_mlx_limits",
    # Memory metrics
    "get_mlx_active_memory_mb",
    "get_mlx_peak_memory_mb",
    "get_mlx_cache_memory_mb",
    "get_mlx_memory_pressure",
    "get_mlx_memory_metrics",
    "format_mlx_memory_snapshot",
    # Cleanup
    "clear_mlx_cache",
    "clear_mlx_cache_debounced",
    "set_cache_limit_with_debounce",
    "mlx_cleanup_sync",
    "mlx_cleanup_aggressive",
    "mlx_cleanup_after",
    "mlx_cleanup_decorator",  # Alias for mlx_cleanup_after (backward compatibility)
    "metal_reclaim",
    "safe_clear_metal_cache",
    # Metal limits
    "get_dynamic_metal_cache_limit",
    "get_metal_limits_status",
    "safe_set_cache_limit",
    "safe_get_cache_limit",
    # Stream
    "get_metal_stream_context",
    # Model cache
    "get_mlx_model",
    "evict_all",
    "get_cache_stats",
    "get_semaphore",
    # Slab
    "MetalSlabPool",
    "release_slab_pool",
    # Prompt cache
    "MLXPromptCache",
    # Embedder buffers
    "MetalBufferPool",
    "get_buffer_pool",
    "init_metal_embedder_buffers",
    "release_metal_embedder_buffers",
    # Shared tensor
    "SharedTensor",
    # mlx_utils decorators
    "mlx_managed",
    "mlx_cleanup_after",
    "get_mlx_memory_stats",
    "reset_metal_peak",
    # Lazy module accessor
    "get_mlx_memory_module",
    # Test aliases
    "_MLX_CACHE_LIMIT",
    "_MLX_WIRED_LIMIT",
]
