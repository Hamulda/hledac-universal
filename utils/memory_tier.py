"""
utils/memory_tier.py — M1/Apple Silicon Memory Tier Detection (canonical)

Canonický modul pro adaptive cache/model sizing podle dostupné RAM.
Používá se pro MLX inference, model pooling, a resource governance.

M1 8GB:  1 model   (strict — Hermes + ModernBERT + GLiNER = 3-4 GB)
M1 16GB: 2 models
M2/M3:   3-4 models

Konzistence s:
  - brain._hermes_cache._adaptive_cache_max_size()
  - core.inference_coordinator._adaptive_model_cache_max()
  - network.ipv6_recon._adaptive_cache_max_size()

Usage:
    from hledac.universal.utils.memory_tier import (
        get_model_cache_max,
        get_lora_cache_max,
        get_adaptive_cache_size,
        is_m1_8gb,
        get_memory_tier_gb,
    )
"""

from __future__ import annotations
from _core import aclose

__all__ = [
    "get_adaptive_cache_size",
    "get_model_cache_max", 
    "get_lora_cache_max",
    "is_m1_8gb",
    "get_memory_tier_gb",
]


def get_memory_tier_gb() -> float:
    """
    Get total system memory in GB.
    
    Returns:
        Total RAM in GB, or 16.0 as safe default on error.
    """
    try:
        import psutil
        return psutil.virtual_memory().total / (1024**3)
    except Exception:
        return 16.0  # safe default


def is_m1_8gb() -> bool:
    """Return True if system has ~8GB RAM (M1 Air constraint)."""
    return get_memory_tier_gb() <= 9.0


def get_adaptive_cache_size() -> int:
    """
    Adaptive model cache size based on available RAM.
    
    M1 8GB:  1 model   (strict — Hermes + ModernBERT + GLiNER = 3-4 GB)
    M1 16GB: 2 models
    M2/M3:   3-4 models
    
    Returns:
        Maximum number of models that fit in memory.
    """
    total_gb = get_memory_tier_gb()
    if total_gb <= 9:
        return 1  # M1 Air 8GB — strict budget
    elif total_gb <= 17:
        return 2
    elif total_gb <= 33:
        return 3
    else:
        return 4


def get_model_cache_max() -> int:
    """Runtime-adaptive model cache max — respects memory tier."""
    return get_adaptive_cache_size()


def get_lora_cache_max() -> int:
    """LoRA cache max: half of model cache max, min 1."""
    return max(1, get_adaptive_cache_size() // 2)
