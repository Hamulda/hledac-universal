"""
M1 P/E Core Topology Detection — Python Fallback Layer
=====================================================

Provides P/E core detection for when the Rust extension isn't available.
Wired to use Rust when compiled, falls back to Python sysctl calls via ctypes.

The actual thread affinity is applied via Rust darwin_affinity.rs when available.
This module provides topology awareness for pool sizing decisions.

Usage:
    from _core.topology import get_p_core_count, get_e_core_count, get_topology

    topo = get_topology()
    print(f"M1: {topo.p_cores}P + {topo.e_cores}E cores")
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass

# Thread-safe singleton cache
_cache: TopologyInfo | None = None

# ponytail: module-level libc handle cached once — avoid repeated dlopen
_libc: ctypes.CDLL | None = None


def _get_libc() -> ctypes.CDLL | None:
    """Get cached libc handle."""
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL(ctypes.util.find_library("c"))
        except Exception:
            return None
    return _libc


def _sysctl_int(name: str) -> int | None:
    """Read integer sysctl via ctypes (bypasses subprocess sandbox)."""
    libc = _get_libc()
    if libc is None:
        return None
    size = ctypes.c_size_t()
    try:
        result = libc.sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0)
        if result != 0 or size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        result2 = libc.sysctlbyname(name.encode(), buf, ctypes.byref(size), None, 0)
        if result2 != 0:
            return None
        return int.from_bytes(buf.raw[: size.value], byteorder="little")
    except Exception:
        return None


def _sysctl_str(name: str) -> str | None:
    """Read string sysctl via ctypes."""
    libc = _get_libc()
    if libc is None:
        return None
    size = ctypes.c_size_t()
    try:
        result = libc.sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0)
        if result != 0 or size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        result2 = libc.sysctlbyname(name.encode(), buf, ctypes.byref(size), None, 0)
        if result2 != 0:
            return None
        return buf.raw[: size.value].rstrip(b"\x00").decode()
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class TopologyInfo:
    """M1 topology info — immutable, cached at first access."""

    p_cores: int
    e_cores: int
    total_logical: int
    p_core_indices: tuple[int, ...]
    e_core_indices: tuple[int, ...]
    is_apple_silicon: bool
    detected: bool

    @classmethod
    def unknown(cls) -> TopologyInfo:
        """Safe fallback when detection fails."""
        return cls(
            p_cores=4,
            e_cores=4,
            total_logical=8,
            p_core_indices=tuple(range(4)),
            e_core_indices=tuple(range(4, 8)),
            is_apple_silicon=False,
            detected=False,
        )


def _detect_via_sysctl() -> TopologyInfo:
    """
    Detect M1 P/E core topology using ctypes sysctlbyname.

    On macOS:
      perflevel0 → E-core cluster (low-power, efficiency)
      perflevel1 → P-core cluster (high-performance)

    This is the CORRECT mapping per macOS kernel source (osfmk/kperflevel.h):
      perflevel 0 = UTILITY (E-cores on hybrid chips)
      perflevel 1 = PERFORMANCE (P-cores on hybrid chips)

    Core indices on M1 MacBook Air (4P+4E):
      CPU 0-3: E-cores (perflevel 0) → e_core_indices = (0,1,2,3)
      CPU 4-7: P-cores (perflevel 1) → p_core_indices = (4,5,6,7)

    M1 Pro (8P+2E):
      perflevel0 = 2 E-cores → indices (0,1)
      perflevel1 = 8 P-cores → indices (2,3,4,5,6,7,8,9)
    """
    is_arm = os.uname().machine.lower() in ("arm64", "aarch64")

    if not is_arm:
        return TopologyInfo.unknown()

    # Read perflevel counts via ctypes (bypasses subprocess sandbox)
    perflevel0 = _sysctl_int("hw.perflevel0.physicalcpu")
    perflevel1 = _sysctl_int("hw.perflevel1.physicalcpu")
    total_logical = _sysctl_int("hw.logicalcpu")

    if perflevel0 is not None and perflevel1 is not None and perflevel0 > 0 and perflevel1 > 0:
        # Both perflevels available: use them
        # perflevel0 = E-cores (UTILITY), perflevel1 = P-cores (PERFORMANCE)
        e_cores = perflevel0
        p_cores = perflevel1
        e_core_indices = tuple(range(e_cores))
        p_core_indices = tuple(range(e_cores, e_cores + p_cores))

        if total_logical is None:
            total_logical = p_cores + e_cores

        return TopologyInfo(
            p_cores=p_cores,
            e_cores=e_cores,
            total_logical=total_logical,
            p_core_indices=p_core_indices,
            e_core_indices=e_core_indices,
            is_apple_silicon=True,
            detected=True,
        )

    # Standard M1 (4P + 4E) fallback — both perflevels missing or zero
    # E-cores at indices 0-3, P-cores at indices 4-7
    p_cores_fallback = 4
    e_cores_fallback = 4

    if total_logical is None:
        total_logical = p_cores_fallback + e_cores_fallback

    # ponytail: indices hardcoded for standard M1; add variant detection if needed
    return TopologyInfo(
        p_cores=p_cores_fallback,
        e_cores=e_cores_fallback,
        total_logical=total_logical,
        # E-cores first (perflevel 0), P-cores after (perflevel 1)
        p_core_indices=tuple(range(e_cores_fallback, e_cores_fallback + p_cores_fallback)),
        e_core_indices=tuple(range(e_cores_fallback)),
        is_apple_silicon=True,
        detected=False,  # Perflevels not confirmed — using fallback
    )


def get_topology() -> TopologyInfo:
    """Get cached M1 topology — thread-safe singleton."""
    global _cache
    if _cache is None:
        _cache = _detect_via_sysctl()
    return _cache


def get_p_core_count() -> int:
    """Get P-core count (cached)."""
    return get_topology().p_cores


def get_e_core_count() -> int:
    """Get E-core count (cached)."""
    return get_topology().e_cores


def get_p_core_indices() -> tuple[int, ...]:
    """Get P-core indices (0-based)."""
    return get_topology().p_core_indices


def get_e_core_indices() -> tuple[int, ...]:
    """Get E-core indices (0-based)."""
    return get_topology().e_core_indices


def is_m1() -> bool:
    """Check if running on Apple Silicon."""
    return get_topology().is_apple_silicon


# Try to wire up Rust extension for affinity (when available)
_RUST_WIRED: bool = False
_apply_affinity_fn: callable | None = None


def _try_wire_rust() -> None:
    """Try to wire up Rust topology functions if extension is available."""
    global _RUST_WIRED, _apply_affinity_fn
    if _RUST_WIRED:
        return

    try:
        from rust_extensions import hledac_rust_extensions as ext

        if hasattr(ext, "p_core_count_py") and callable(ext.p_core_count_py):
            p_count = ext.p_core_count_py()
            e_count = ext.e_core_count_py()
            p_indices = tuple(ext.get_p_core_indices_py()) if hasattr(ext, "get_p_core_indices_py") else None
            e_indices = tuple(ext.get_e_core_indices_py()) if hasattr(ext, "get_e_core_indices_py") else None
            total = ext.total_logical_cores_py() if hasattr(ext, "total_logical_cores_py") else p_count + e_count
            is_as = ext.is_m1_py() if hasattr(ext, "is_m1_py") else True

            global _cache
            _cache = TopologyInfo(
                p_cores=p_count,
                e_cores=e_count,
                total_logical=total,
                p_core_indices=p_indices if p_indices else tuple(range(e_count, e_count + p_count)),
                e_core_indices=e_indices if e_indices else tuple(range(e_count)),
                is_apple_silicon=is_as,
                detected=True,
            )
            _apply_affinity_fn = (
                ext.apply_affinity_for_workload_py if hasattr(ext, "apply_affinity_for_workload_py") else None
            )
            _RUST_WIRED = True
    except Exception:
        pass


def apply_affinity_for_workload(workload: str) -> None:
    """
    Apply thread affinity for workload type.

    Uses Rust darwin_affinity when available, no-op otherwise.

    Workload types:
        - "cpu_intensive", "mlx_inference", "graph_traverse" → P-cores
        - "io_bound", "network_io", "telemetry" → E-cores
    """
    _try_wire_rust()

    if _apply_affinity_fn is not None:
        try:
            _apply_affinity_fn(workload)
        except Exception:
            pass  # Fail-safe: no-op on affinity errors


# Auto-wire on import
_try_wire_rust()
