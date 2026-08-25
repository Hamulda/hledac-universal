"""
utils/m1_resource.py — M1 Unified Resource Factory (ISSUE #3 fix)

Jediný canonical zdroj pre dynamické výpočty M1 Metal cache limitov.
Tento modul nahrádza dve duplicitné implementácie:
- utils/mlx_cache.py::get_dynamic_metal_cache_limit (F265/F267/F267H era)
- utils/mlx_memory/_core.py::get_dynamic_metal_cache_limit (P2-5 fix era)

M1 8GB UMA budget (súčet = 6.25 GiB):
macOS baseline:   ~2.5 GiB
Orchestrátor:     ~1.0 GiB
LLM (Hermes-3):  ~2.0 GiB
KV cache:        ~0.75 GiB
Metal cache:     ~0.5–1.1 GiB  (dynamic ceiling 1.5 GiB)
Metal wired:      768 MiB       (fixed, cannot be swapped)

Formula (ISSUE #3 UNIFORM):
normal:     int(min(max(available_gb * 0.20, 0.5), 1.5) * 2**30)
emergency:   int(min(max(available_gb * 0.20, 0.25), 1.5) * 2**30)
thermal:     výsledok * headroom_scale (0.25 / 0.5 / 1.0 podľa teploty)
hard floor:  256 MiB (nikdy nepadnúť pod to ani v emergency+thermal)

GHOST_INVARIANT: Metal cache limit ceiling = 1.5 GiB na 8GB machines.
"""

from __future__ import annotations

import psutil

# Canonical constants (matching _core.py and mlx_cache.py historical values)
_METAL_CACHE_EMERGENCY_FLOOR_BYTES: int = 256 * 1024 * 1024   # 256 MiB
_METAL_CACHE_NORMAL_FLOOR_GIB: float = 0.5                     # 512 MiB
_METAL_CACHE_CEILING_GIB: float = 1.5                         # 1.5 GiB hard ceiling


def get_dynamic_metal_cache_limit(
    uma_state: str | None = None,
    thermal_headroom: float = 1.0,
) -> int:
    """
    Compute the Metal cache limit dynamically based on available system memory.

    This is the SINGLE canonical implementation replacing:
    - utils/mlx_cache.py::get_dynamic_metal_cache_limit (original F265/F267 era)
    - utils/mlx_memory/_core.py::get_dynamic_metal_cache_limit (P2-5 fix era)

    Parameters
    ----------
    uma_state : str | None
        Optional UMA state string among ("ok", "soft_warn", "warn", "critical", "emergency").
        When "emergency", uses 256 MiB floor instead of 512 MiB.
        None is treated as "normal" (512 MiB floor).
    thermal_headroom : float
        Float 0.0–1.0, where 1.0 = no throttling.
        On M1 MacBook Air (fanless):
        - >= 0.5: nominal operation
        - 0.3–0.5: mild throttle (>70°C), cache *= 0.5
        - < 0.3:  severe throttle (>85°C), cache *= 0.25

    Returns
    -------
    int
        Metal cache limit in bytes, clamped [256 MiB, 1.5 GiB].

    M1 8GB budget pri 5.5 GiB available:
    cache = min(5.5 * 0.2, 1.5) = min(1.1, 1.5) = 1.1 GiB
    → model(2GB) + cache(1.1GB) + KV(0.75GB) = ~3.85GB MLX footprint
    → leaving ~4.15GB for macOS → stays in warn zone, not critical.

    HW-01 / ISSUE-013: Under thermal pressure, Metal cache is reduced to free
    up memory bandwidth. On M1 MacBook Air (fanless), Metal and CPU share the
    same heatsink — sustained inference at >70°C throttles both.
    """
    is_emergency = uma_state == "emergency"

    # Floor: 256 MiB emergency / 512 MiB normal
    floor_gib = 0.25 if is_emergency else _METAL_CACHE_NORMAL_FLOOR_GIB

    try:
        available_bytes = psutil.virtual_memory().available
        available_gb = available_bytes / (1024 ** 3)

        # Core formula: 20% of available, clamped [floor, 1.5 GiB]
        raw = available_gb * 0.20
        clamped = min(max(raw, floor_gib), _METAL_CACHE_CEILING_GIB)

        # HW-01 thermal headroom feedback
        if thermal_headroom < 0.3:        # Severe throttle
            clamped *= 0.25
        elif thermal_headroom < 0.5:      # Mild throttle
            clamped *= 0.5

        # Hard floor: never below 256 MiB even in emergency+thermal
        result_gib = max(clamped, 0.25)  # 256 MiB = 0.25 GiB
        return int(result_gib * (1024 ** 3))

    except Exception:
        # Fallback: 1.5 GiB ceiling
        return int(_METAL_CACHE_CEILING_GIB * (1024 ** 3))
