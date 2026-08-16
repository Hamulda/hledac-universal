"""
Canonical async-friendly system memory metrics — Issue #29.

Provides:


  - Async wrappers over resource_governor's TTL-cached psutil reads
  - Rust-native memory probes (sysinfo, no psutil syscall)
  - Swap memory tracking
  - Unified API: system_memory(), process_rss(), memory_pressure()

All psutil access goes through this module. Raw psutil.virtual_memory() /
psutil.swap_memory() in application code is a bug — use these instead.

M1 8GB safe: ~0 bytes extra RAM, async by design.
"""

import asyncio
import logging
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import TYPE_CHECKING

from hledac.universal._core.resource_governor import _get_cached_psutil_async, _read_virtual_memory_sync, _read_swap_memory_sync
from _core import aclose

if TYPE_CHECKING:
    import psutil
    from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class SystemMemory(Struct, frozen=True):
    """Immutable snapshot of system memory."""

    total_gib: float
    available_gib: float
    used_gib: float
    percent: float  # 0-100

    @classmethod
    def from_psutil(cls, vm: Any) -> "SystemMemory":
        """Build from psutil.virtual_memory() result."""
        total = getattr(vm, "total", 0)
        available = getattr(vm, "available", 0)
        used = total - available
        return cls(
            total_gib=total / (1024**3),
            available_gib=available / (1024**3),
            used_gib=used / (1024**3),
            percent=getattr(vm, "percent", 0.0),
    )


class SwapMemory(Struct, frozen=True):
    """Immutable snapshot of swap usage."""

    total_gib: float
    used_gib: float
    percent: float  # 0-100

    @classmethod
    def from_psutil(cls, sm: Any) -> "SwapMemory":
        """Build from psutil.swap_memory() result."""
        return cls(
            total_gib=getattr(sm, "total", 0) / (1024**3),
            used_gib=getattr(sm, "used", 0) / (1024**3),
            percent=getattr(sm, "percent", 0.0),
    )


# ---------------------------------------------------------------------------
# Async canonical entry points — all psutil access MUST go through here
# ---------------------------------------------------------------------------


async def system_memory() -> SystemMemory:
    """
    Async, cached system memory via resource_governor's TTL cache.

    Non-blocking: offloads to thread, returns cached value on subsequent
    calls within TTL window.  No event-loop stall.

    Returns SystemMemory with (total_gib, available_gib, used_gib, percent).
    """
    try:
        vm = await _get_cached_psutil_async("virtual_memory", _read_virtual_memory_sync)
        if vm is None:
            return SystemMemory(0.0, 0.0, 0.0, 0.0)
        return SystemMemory.from_psutil(vm)
    except Exception as e:
        logger.debug(f"system_memory() failed: {e}")
        return SystemMemory(0.0, 0.0, 0.0, 0.0)


async def swap_memory() -> SwapMemory:
    """
    Async, cached swap memory via resource_governor's TTL cache.

    Non-blocking: offloads to thread, returns cached value within TTL window.
    """
    try:
        sm = await _get_cached_psutil_async("swap_memory", _read_swap_memory_sync)
        if sm is None:
            return SwapMemory(0.0, 0.0, 0.0)
        return SwapMemory.from_psutil(sm)
    except Exception as e:
        logger.debug(f"swap_memory() failed: {e}")
        return SwapMemory(0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Rust-native probes — zero syscall, no psutil dependency
# ---------------------------------------------------------------------------


async def process_rss_gib() -> float:
    """
    Process RSS in GiB via Rust (sysinfo / proc_pidinfo on macOS).

    Uses rust.memory.get_process_rss_gib() — no psutil, no syscall stall.
    Falls back to 0.0 on error.
    """
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available:
            return rust.memory.get_process_rss_gib()
    except Exception:  # noqa: BLE001
        pass
    return 0.0


async def available_memory_gib() -> float:
    """
    System available memory in GiB via Rust sysinfo.

    Uses rust.memory.get_available_memory_gib() — no psutil.
    """
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available:
            return rust.memory.get_available_memory_gib()
    except Exception:  # noqa: BLE001
        pass
    return 0.0


async def memory_pressure_level() -> int:
    """
    M1 memory pressure level 0-2 (normal/elevated/critical).

    Uses rust.memory.memory_pressure_level() — no psutil.

    Thresholds: normal < 4 GiB, elevated 4-5.5 GiB, critical > 5.5 GiB.
    """
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available:
            return rust.memory.memory_pressure_level()
    except Exception:  # noqa: BLE001
        pass
    return 0


async def metal_active_memory_gib() -> float:
    """
    MLX Metal active memory in GiB.

    Uses rust.memory.get_metal_active_memory_gib() — GIL-protected Python call.
    Returns 0.0 when MLX unavailable.
    """
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available:
            return rust.memory.get_metal_active_memory_gib()
    except Exception:  # noqa: BLE001
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Sync fallbacks — for use in __init__ / non-async contexts ONLY
# ---------------------------------------------------------------------------


def system_memory_sync() -> SystemMemory:
    """
    Sync fallback for non-async contexts (e.g., __init__, atexit).

    Uses resource_governor's thread-safe TTL cache directly.
    """
    # Deferred import to avoid circular reference at module load
    from hledac.universal._core.resource_governor import _get_cached_psutil, _read_virtual_memory_sync

    try:
        vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
        if vm is None:
            return SystemMemory(0.0, 0.0, 0.0, 0.0)
        return SystemMemory.from_psutil(vm)
    except Exception as e:
        logger.debug(f"system_memory_sync() failed: {e}")
        return SystemMemory(0.0, 0.0, 0.0, 0.0)


def swap_memory_sync() -> SwapMemory:
    """Sync fallback for non-async contexts."""
    from hledac.universal._core.resource_governor import _get_cached_psutil, _read_swap_memory_sync

    try:
        sm = _get_cached_psutil("swap_memory", _read_swap_memory_sync)
        if sm is None:
            return SwapMemory(0.0, 0.0, 0.0)
        return SwapMemory.from_psutil(sm)
    except Exception as e:
        logger.debug(f"swap_memory_sync() failed: {e}")
        return SwapMemory(0.0, 0.0, 0.0)
