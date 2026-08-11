"""
core.memory — INTEL/MEMORY GOVERNANCE SSOT (Issue #38, A5-04)
==============================================================

Single source of truth pro SYSTEM-WIDE memory metriky.
Rust (memory.rs) je autoritativní zdroj; Python jsou jen tenké wrappers.

TENTO MODUL vs. OSTATNÍ PAMĚŤOVÉ MODULY (A5-04 consolidation)
==============================================================
| Modul                        | Zodpovědnost                        |
|------------------------------|--------------------------------------|
| core.memory                  | System-wide: RSS, dostupná RAM,     |
|                              | celková RAM, pressure level (Rust)  |
| core.rust_backend.memory     | DuckDB bridge: domain factory pro   |
|                              | _RustMemoryDomain/_PythonMemoryDomain|
| utils.mlx_memory._core       | MLX-specific: active_mb, peak_mb,  |
|                              | cache_mb, pressure_pct (Metal API)  |

 ŽÁDNÝ PŘEKRYV FUNKCÍ — každý modul má jinou roli.

MLX METRICS: Pro MLX-specific metriky použij:
    from utils.mlx_memory import get_mlx_memory_metrics
    # dict: active_mb, peak_mb, cache_mb, pressure_pct, pressure_level

SYSTEM METRICS: Pro system-wide metriky použij:
    from core.memory import get_memory_snapshot
    # dict: rss_bytes, rss_gib, available_memory_gib, total_memory_gib,
    #       metal_active_bytes, metal_active_gib, pressure_level

Import: from core.memory import get_memory_snapshot
"""

import logging
from typing import Any

__all__ = [
    "get_memory_snapshot",
    "get_process_rss_gib",
    "get_available_memory_gib",
    "get_metal_active_memory_bytes",
    "get_metal_active_memory_gib",
    "memory_pressure_level",
    "peak_rss_bytes",
    "set_memory_pressure_thresholds",  # A5-01: sync Rust thresholds from Python SSOT
]

logger = logging.getLogger(__name__)

# Lazy flag: True pokud Rust extension dostupná
_RUST_AVAILABLE: bool = False
_RUST_LOADED: bool = False


def _ensure_rust() -> bool:
    """Lazy load Rust extension. Returns True if available."""
    global _RUST_AVAILABLE, _RUST_LOADED
    if _RUST_LOADED:
        return _RUST_AVAILABLE
    _RUST_LOADED = True
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    raw = rust.raw
    _rust_snapshot = raw.get_memory_snapshot
    _rust_rss = raw.get_process_rss_gib
    _rust_avail = raw.get_available_memory_gib
    _rust_metal = raw.get_metal_active_memory_bytes
    _rust_metal_gib = raw.get_metal_active_memory_gib
    _rust_pressure = raw.memory_pressure_level
    _rust_peak = raw.peak_rss_bytes
    _rust_set_thresholds = raw.set_memory_pressure_thresholds
    _RUST_AVAILABLE = all([_rust_snapshot, _rust_rss, _rust_avail, _rust_metal, _rust_pressure])
    if _RUST_AVAILABLE:
        globals()["_rust_snapshot"] = _rust_snapshot
        globals()["_rust_rss"] = _rust_rss
        globals()["_rust_avail"] = _rust_avail
        globals()["_rust_metal"] = _rust_metal
        globals()["_rust_metal_gib"] = _rust_metal_gib
        globals()["_rust_pressure"] = _rust_pressure
        globals()["_rust_peak"] = _rust_peak
        globals()["set_memory_pressure_thresholds"] = _rust_set_thresholds
        logger.debug("[memory] Rust SSOT loaded OK")
    else:
        logger.debug("[memory] Rust extension unavailable, using Python fallback")
    return _RUST_AVAILABLE


# ---------------------------------------------------------------------------
# Public API — vždy fail-safe (0 / 0.0 na chybu)
# ---------------------------------------------------------------------------


def get_memory_snapshot() -> dict[str, Any]:
    """
    Canonical memory snapshot — všechny metriky v jednom volání.

    Returns:
        dict s klíči:
        - rss_bytes: u64 — process RSS (bajtů)
        - rss_gib: f64 — process RSS (GiB)
        - peak_rss_bytes: u64 — peak RSS od startu procesu
        - available_memory_gib: f64 — dostupná systémová RAM (GiB)
        - total_memory_gib: f64 — celková RAM (GiB)
        - metal_active_bytes: u64 — MLX Metal active memory (bajtů)
        - metal_active_gib: f64 — MLX Metal active memory (GiB)
        - pressure_level: u8 — 0=normal, 1=elevated, 2=critical

    Fail-safe: vrací {"error": str} pokud vše selže.
    """
    if _ensure_rust():
        try:
            return globals()["_rust_snapshot"]()
        except Exception as exc:
            logger.debug("[memory] Rust snapshot failed: %s", exc)
    return _fallback_snapshot()


def get_process_rss_gib() -> float:
    """Process RSS v GiB. 0.0 na chybu."""
    if _ensure_rust():
        try:
            return globals()["_rust_rss"]()
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def get_available_memory_gib() -> float:
    """Dostupná systémová RAM v GiB. 0.0 na chybu."""
    if _ensure_rust():
        try:
            return globals()["_rust_avail"]()
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def get_metal_active_memory_bytes() -> int:
    """MLX Metal active memory v bytech. 0 pokud MLX nedostupný."""
    if _ensure_rust():
        try:
            return globals()["_rust_metal"]()
        except Exception:  # noqa: BLE001
            pass
    return 0


def get_metal_active_memory_gib() -> float:
    """MLX Metal active memory v GiB. 0.0 pokud MLX nedostupný."""
    if _ensure_rust():
        try:
            return globals()["_rust_metal_gib"]()
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def memory_pressure_level() -> int:
    """
    Memory pressure level (0=normal, 1=elevated, 2=critical).

    Thresholds (M1 8GB):
        0 (normal)     — RSS < 4.0 GiB
        1 (elevated)  — RSS 4.0–5.5 GiB
        2 (critical)   — RSS > 5.5 GiB
    """
    if _ensure_rust():
        try:
            return globals()["_rust_pressure"]()
        except Exception:  # noqa: BLE001
            pass
    return 0


def peak_rss_bytes() -> int:
    """Peak RSS v bytech od startu procesu. 0 na chybu."""
    if _ensure_rust():
        try:
            return globals()["_rust_peak"]()
        except Exception:  # noqa: BLE001
            pass
    return 0


# ---------------------------------------------------------------------------
# Fallback — používá psutil když Rust není dostupný
# ---------------------------------------------------------------------------


def _fallback_snapshot() -> dict[str, Any]:
    """Fallback pomocí psutil když Rust extension není dostupná."""
    try:
        import psutil
        process = psutil.Process()
        vm = psutil.virtual_memory()

        rss = process.memory_info().rss
        metal_bytes = _get_metal_active_python()

        return {
            "rss_bytes": rss,
            "rss_gib": rss / (1024**3),
            "peak_rss_bytes": rss,  # psutil nemá peak
            "available_memory_gib": vm.available / (1024**3),
            "total_memory_gib": vm.total / (1024**3),
            "metal_active_bytes": metal_bytes,
            "metal_active_gib": metal_bytes / (1024**3),
            "pressure_level": _calc_pressure_level(rss),
        }
    except Exception as exc:
        logger.debug("[memory] fallback snapshot failed: %s", exc)
        return {"error": str(exc)}


def _get_metal_active_python() -> int:
    """Python-only MLX Metal active memory probe."""
    try:
        import mlx.core as mx
        if hasattr(mx, "get_active_memory"):
            return mx.get_active_memory()
        if hasattr(mx.metal, "get_active_memory"):
            return mx.metal.get_active_memory()
    except Exception:  # noqa: BLE001
        pass
    return 0


def _calc_pressure_level(rss_bytes: int) -> int:
    """Calculate pressure level from RSS bytes."""
    SOFT = 4 * 1024**3  # 4 GiB
    # P0-2 Fix: Was `(11 * 1024 // 2) * 1024**3` = 5.6 TiB (!)
    # Operator precedence: // binds before *, so 11*1024//2 = 5632, then * 1024**3
    # Correct: 5.5 GiB = int(5.5 * 1024**3) using SSOT constant
    from hledac.universal.utils.uma_budget import UmaBudget
    HARD = int(UmaBudget.MISSION_PEAK_RSS_GIB * 1024**3)  # 5.5 GiB
    if rss_bytes > HARD:
        return 2
    elif rss_bytes > SOFT:
        return 1
    return 0
