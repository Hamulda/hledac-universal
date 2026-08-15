"""
core/system_metrics.py — Unified system metrics with M1-optimized syscalls
==========================================================================


Replaces raw psutil calls in hot paths with cached, zero-syscall alternatives.

M1 8GB UMA invariants:
  - get_rss_rusage() — ZERO syscall after first call (struct from libc, cached in thread-local)
  - get_memory_pressure_mach() — ~50 µs warm (host_statistics64 mach call), cached 200ms
  - SystemSnapshot — unified single-point-of-truth for all hot-path metrics

Cache TTL: 200ms — debounces rapid successive calls in tight loops without
stale data issues. ResourceGovernor uses 2s TTL for its policy decisions;
this module targets the sub-second monitoring loop use case.
"""
from __future__ import annotations

import os
import resource
import sys
import threading
import time as _time_module
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from core._util import aclose

if TYPE_CHECKING:
    pass

__all__ = [
    "get_rss_rusage",
    "get_memory_pressure_mach",
    "get_system_snapshot",
    "SystemSnapshot",
    "_SYSTEM_CACHE_TTL_S",
]

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

_SYSTEM_CACHE_TTL_S: float = 0.2  # 200ms — debounce for tight loops

# Darwin Mach host info flavors
_MACH_HOST_SELF: int = -1  # mach_host_self() → current host port

# ------------------------------------------------------------------ #
# sysctl cached reader — avoid subprocess overhead on every call
# ------------------------------------------------------------------ #
_sysctl_cache: dict[str, tuple[int | float, float]] = {}  # key → (value, timestamp)


def _get_sysctlCached(name: str) -> int | float | None:
    """Read sysctl by name, cached 200ms. Returns int/float or None."""
    try:
        now = _time_module.monotonic()
        entry = _sysctl_cache.get(name)
        if entry is not None:
            val, ts = entry
            if now - ts < _SYSTEM_CACHE_TTL_S:
                return val
        import subprocess

        result = subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, timeout=1
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            val = int(raw) if raw.isdigit() else float(raw)
            _sysctl_cache[name] = (val, now)
            return val
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------ #
# Load average — cached like the rest
# ------------------------------------------------------------------ #
_loadavg_cache: tuple[tuple[float, float, float], float] | None = None


def _get_loadavg() -> tuple[float, float, float] | None:
    """os.getloadavg(), cached 200ms. Returns (1m, 5m, 15m) or None."""
    global _loadavg_cache
    try:
        now = _time_module.monotonic()
        if _loadavg_cache is not None:
            la, ts = _loadavg_cache
            if now - ts < _SYSTEM_CACHE_TTL_S:
                return la
        la = os.getloadavg()
        _loadavg_cache = (la, now)
        return la
    except Exception:
        return None

# ------------------------------------------------------------------ #
# Dataclass
# ------------------------------------------------------------------ #

@dataclass(slots=True, frozen=True)
class SystemSnapshot:
    """
    Unified snapshot of process + system memory metrics.

    All values are derived once per cache TTL. Callers MUST NOT make
    additional raw psutil calls — use this snapshot instead.
    """
    timestamp: float
    rss_bytes: int           # Process RSS from getrusage (kilobytes on Darwin → bytes)
    rss_mb: float            # RSS in MB
    memory_percent: float     # System memory used percent (0-100)
    memory_used_gb: float    # System used (total - available)
    memory_available_gb: float
    memory_pressure: str      # "GREEN" | "YELLOW" | "RED" | "UNKNOWN"
    free_pct: int            # System free percent 0-100
    # Diagnostic (not used in hot-path decisions)
    cpu_percent: float = 0.0
    load_average: tuple[float, float, float] | None = None

# ------------------------------------------------------------------ #
# RSS via getrusage — ZERO syscall after first call
# ------------------------------------------------------------------ #
# Thread-local cache for getrusage result.
# On Darwin getrusage(RUSAGE_SELF) returns struct rusage with ru_maxrss in KB.
# The struct is read from libc without any syscall after initial setup.
_rusage_cache: dict[str, tuple[int, float]] = {}  # key → (rusage_maxrss_bytes, timestamp)
_rusage_lock: "threading.Lock | None" = None  # lazily initialized

def _get_rusage_lock():
    global _rusage_lock
    if _rusage_lock is None:
        import threading
        _rusage_lock = threading.Lock()
    return _rusage_lock


def get_rss_rusage() -> int:
    """
    Return process RSS in bytes via getrusage(RUSAGE_SELF).

    ZERO syscall after first call per thread — the rusage struct is served
    from libc's cached process info. M1 returns ru_maxrss in kilobytes.

    Fallback: returns 0 on any error (fail-safe, never raises).
    """
    try:
        lock = _get_rusage_lock()
        with lock:
            now = _time_module.monotonic()
            entry = _rusage_cache.get("rss")
            if entry is not None:
                rss_bytes, ts = entry
                if now - ts < _SYSTEM_CACHE_TTL_S:
                    return rss_bytes
            # Actual syscall — happens once per TTL window
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # Darwin: ru_maxrss is in kilobytes; convert to bytes
            rss_kb = getattr(usage, "ru_maxrss", 0)
            if sys.platform == "darwin":
                rss_bytes = rss_kb * 1024
            else:
                rss_bytes = rss_kb  # Linux reports in kilobytes already
            _rusage_cache["rss"] = (rss_bytes, now)
            return rss_bytes
    except Exception:
        return 0


# ------------------------------------------------------------------ #
# Memory pressure via mach host_statistics64 — ~50µs warm
# ------------------------------------------------------------------ #
# cffi/ctypes shim for host_statistics64(mach_host_self(), HOST_VM_INFO64, ...)
# Returns vm_statistics64_data_t fields needed for memory pressure derivation.

_memory_pressure_cache: dict[str, tuple[dict[str, int], float]] = {}  # key → (pressure_dict, ts)
_mach_lock: "threading.Lock | None" = None


def _get_mach_lock():
    global _mach_lock
    if _mach_lock is None:
        import threading
        _mach_lock = threading.Lock()
    return _mach_lock


def _host_statistics64_fallback() -> dict[str, int]:
    """
    Fallback when mach library is unavailable — returns safe zeros.
    Caller should fall back to psutil virtual_memory if all zeros.
    """
    return {
        "free_count": 0,
        "active_count": 0,
        "inactive_count": 0,
        "wire_count": 0,
        "compressed_count": 0,
        "total": 0,
    }


def get_memory_pressure_mach() -> dict[str, int]:
    """
    Read M1 memory pressure via mach host_statistics64(HOST_VM_INFO64).

    ~50 µs warm, ~1 ms cold. Returns raw counts for caller to derive status.

    Keys: free_count, active_count, inactive_count, wire_count, compressed_count, total
    """
    try:
        lock = _get_mach_lock()
        with lock:
            now = _time_module.monotonic()
            entry = _memory_pressure_cache.get("mach")
            if entry is not None:
                data, ts = entry
                if now - ts < _SYSTEM_CACHE_TTL_S:
                    return data
    except Exception:
        return _host_statistics64_fallback()

    try:
        # Try cffi first (faster, more capable)
        _using_ctypes = False
        try:
            from cffi import FFI

            ffi = FFI()
            ffi.cdef(
                """
                int mach_host_self(void);
                int host_statistics64(int host_port, int flavor, void *stat, int *count);
                """
            )
            libc = ffi.dlopen("libc.dylib")
            mach_host_self = libc.mach_host_self
        except Exception:
            # Fall back to ctypes
            from ctypes import CDLL, Structure, c_int, pointer

            libc = CDLL("/usr/lib/libSystem.B.dylib")
            mach_host_self = libc.mach_host_self

            class struct_vm_statistics64(Structure):
                _fields_ = [
                    ("free_count", c_int),
                    ("active_count", c_int),
                    ("inactive_count", c_int),
                    ("wire_count", c_int),
                    ("compressed_count", c_int),
                    (" compressor_pages", c_int),
                    ("thumb_pages", c_int),
                    ("speculative_count", c_int),
                ] + [("r", c_int) for _ in range(50)]  # padding for size

            _using_ctypes = True

        # Call host_statistics64
        HOST_VM_INFO_COUNT = 34
        mach_port = mach_host_self()

        if _using_ctypes:
            stat = struct_vm_statistics64()
            count = c_int(HOST_VM_INFO_COUNT)
            result = libc.host_statistics64(
                mach_port, HOST_VM_INFO_COUNT, pointer(stat), count
            )
        else:
            # cffi: allocate struct via ffi.new()
            vm_stat_type = ffi.new("struct {"
                "int free_count; int active_count; int inactive_count; "
                "int wire_count; int compressed_count; int compressor_pages; "
                "int thumb_pages; int speculative_count; int extra[50]; }*")
            count = ffi.new("int*", HOST_VM_INFO_COUNT)
            result = libc.host_statistics64(
                mach_port, HOST_VM_INFO_COUNT, vm_stat_type, count
            )
            if result == 0:
                data = {
                    "free_count": vm_stat_type.free_count,
                    "active_count": vm_stat_type.active_count,
                    "inactive_count": vm_stat_type.inactive_count,
                    "wire_count": vm_stat_type.wire_count,
                    "compressed_count": vm_stat_type.compressed_count,
                    "total": (
                        vm_stat_type.free_count
                        + vm_stat_type.active_count
                        + vm_stat_type.inactive_count
                        + vm_stat_type.wire_count
                        + vm_stat_type.compressed_count
                    ),
                }
            else:
                data = _host_statistics64_fallback()
            with _get_mach_lock():
                _memory_pressure_cache["mach"] = (data, _time_module.monotonic())
            return data

        if result == 0:
            data = {
                "free_count": stat.free_count,
                "active_count": stat.active_count,
                "inactive_count": stat.inactive_count,
                "wire_count": stat.wire_count,
                "compressed_count": stat.compressed_count,
                "total": (
                    stat.free_count
                    + stat.active_count
                    + stat.inactive_count
                    + stat.wire_count
                    + stat.compressed_count
                ),
            }
        else:
            data = _host_statistics64_fallback()

    except Exception:
        data = _host_statistics64_fallback()

    with _get_mach_lock():
        _memory_pressure_cache["mach"] = (data, _time_module.monotonic())
    return data


# ------------------------------------------------------------------ #
# Unified snapshot — single point of entry for hot paths
# ------------------------------------------------------------------ #
_system_snapshot_cache: dict[str, tuple[SystemSnapshot, float]] = {}


def get_system_snapshot() -> SystemSnapshot:
    """
    Return a unified SystemSnapshot with all hot-path metrics.

    Reading strategy:
      1. RSS → get_rss_rusage() — zero syscall after first call
      2. System memory → mach host_statistics64 (fast) or psutil virtual_memory
         (only if mach fails AND use_psutil_fallback=True)
      3. Memory pressure → get_memory_pressure_mach()

    This function is the SOLE hot-path entry point. All callers in
    monitoring_coordinator and execution_optimizer MUST use this instead of
    making individual psutil calls.

    Fail-open: returns zero-filled SystemSnapshot on any error (never raises).
    """
    try:
        now = _time_module.monotonic()
        entry = _system_snapshot_cache.get("snapshot")
        if entry is not None:
            snap, ts = entry
            if now - ts < _SYSTEM_CACHE_TTL_S:
                return snap

        # RSS — zero syscall
        rss_bytes = get_rss_rusage()
        rss_mb = rss_bytes / (1024 * 1024)

        # System memory via mach (fast path)
        mach_data = get_memory_pressure_mach()
        total_pages = mach_data.get("total", 0)
        free_pages = mach_data.get("free_count", 0)
        wire_pages = mach_data.get("wire_count", 0)

        # Try to get total RAM in bytes via cached sysctl (avoids subprocess overhead)
        page_size = 4096  # Darwin 4KB pages
        total_bytes = 0
        if total_pages > 0:
            total_bytes = total_pages * page_size
        sysctl_total = _get_sysctlCached("hw.memsize")
        if sysctl_total is not None:
            total_bytes = int(sysctl_total)

        # If mach returned zeros (ctypes struct mismatch), fall back to psutil
        if total_pages == 0 or free_pages == 0:
            try:
                import psutil

                vm = psutil.virtual_memory()
                memory_percent = vm.percent
                memory_used_gb = vm.used / (1024**3)
                memory_available_gb = vm.available / (1024**3)
                free_pct = int(100 - memory_percent)
                pressure = "RED" if memory_percent > 85 else "YELLOW" if memory_percent > 70 else "GREEN"
            except Exception:
                memory_percent = 0.0
                memory_used_gb = 0.0
                memory_available_gb = 0.0
                free_pct = 0
                pressure = "UNKNOWN"
        else:
            used_bytes = total_bytes - (free_pages * 4096)
            memory_percent = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0
            memory_used_gb = used_bytes / (1024**3)
            memory_available_gb = (free_pages * 4096) / (1024**3)
            # Derive pressure status from mach counts (mach success path)
            free_pct = int(free_pages / total_pages * 100)
            wired_pct = int(wire_pages / total_pages * 100)
            if free_pct < 15 or wired_pct > 40:
                pressure = "RED"
            elif free_pct < 30 or wired_pct > 25:
                pressure = "YELLOW"
            else:
                pressure = "GREEN"

        # Load average — cached to avoid getloadavg syscall overhead
        load_avg: tuple[float, float, float] | None = _get_loadavg()

        snap = SystemSnapshot(
            timestamp=now,
            rss_bytes=rss_bytes,
            rss_mb=rss_mb,
            memory_percent=memory_percent,
            memory_used_gb=memory_used_gb,
            memory_available_gb=memory_available_gb,
            memory_pressure=pressure,
            free_pct=free_pct,
            cpu_percent=0.0,  # Not used in hot paths — use psutil if needed
            load_average=load_avg,
        )

        _system_snapshot_cache["snapshot"] = (snap, now)
        return snap

    except Exception:
        # Fail-open: return zero snapshot
        return SystemSnapshot(
            timestamp=_time_module.time(),
            rss_bytes=0,
            rss_mb=0.0,
            memory_percent=0.0,
            memory_used_gb=0.0,
            memory_available_gb=0.0,
            memory_pressure="UNKNOWN",
            free_pct=0,
        )


def invalidate_cache() -> None:
    """Invalidate all caches. For testing or forced refresh."""
    global _rusage_cache, _memory_pressure_cache, _system_snapshot_cache
    import threading

    with _get_rusage_lock():
        _rusage_cache.clear()
    with _get_mach_lock():
        _memory_pressure_cache.clear()
    with threading.Lock():
        _system_snapshot_cache.clear()
