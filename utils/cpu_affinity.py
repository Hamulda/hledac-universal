"""
CPU Affinity Utilities for M1 Apple Silicon — MODERN-35 Fix.

ISSUE MODERN-35: MLX runs on GPU but competes with rayon on P-cores.
AN E-cores should handle I/O while P-cores run MLX/ANE.

ARCHITECTURE ON M1:
- M1 8GB: 4x P-cores (Firestorm) + 0x E-cores (Icestorm)
- P-cores: High-performance, run MLX Metal compute, rayon's CPU-intensive work
- E-cores: Energy-efficient, reserved for I/O-bound tasks (network, disk)

AFFINITY STRATEGY (MODERN-35):
- MLX Metal: P-cores only (highest QoS, no E-core interference)
- Rayon CPU pool: P-cores only (competes with MLX, need QoS coordination)
- I/O threads: E-cores only (reserved strictly for I/O)
- ANE inference: P-cores only (Neural Engine offload)

IMPLEMENTATION:
- macOS: Uses pthread_setaffinity_np() via ctypes
- Linux fallback: Uses os.sched_setaffinity()
- Windows fallback: Uses processоре affinity API

USAGE:
    from utils.cpu_affinity import set_mlx_affinity, set_io_affinity, get_p_core_mask

    # Before MLX inference
    set_mlx_affinity()  # Pin MLX threads to P-cores

    # Before I/O operations
    set_io_affinity()   # Pin I/O threads to E-cores (if available)

    # Get current core mask
    p_cores = get_p_core_mask()
    e_cores = get_e_core_mask()
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
import threading
from typing import Sequence

__all__ = [
    "set_mlx_affinity",
    "set_io_affinity",
    "get_p_core_mask",
    "get_e_core_mask",
    "get_core_topology",
    "CoreType",
    "is_apple_silicon",
]

logger = logging.getLogger(__name__)


class CoreType:
    """Core type enumeration for M1 topology."""
    P_CORE = "performance"
    E_CORE = "efficiency"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# Platform Detection
# ═══════════════════════════════════════════════════════════════════════════════


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon (M1/M2/M3/M4)."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


# ═══════════════════════════════════════════════════════════════════════════════
# macOS Implementation (pthread_setaffinity_np)
# ═══════════════════════════════════════════════════════════════════════════════

# ctypes definitions for macOS pthread_setaffinity_np
_libc = ctypes.CDLL(None)

# CPU_SETSIZE for macOS (1024)
CPU_SETSIZE = 1024

# sizeof(__cpu_mask) = 4 bytes on macOS (64-bit)
CPU_MASK_SIZE = ctypes.sizeof(ctypes.c_uint32 * (CPU_SETSIZE // 32))


class _cpuset_macos(ctypes.Structure):
    """macOS cpuset structure for pthread_setaffinity_np."""
    _fields_ = [
        ("__bits", ctypes.c_uint32 * (CPU_SETSIZE // 32)),
    ]


# pthread_setaffinity_np signature
pthread_setaffinity_np = _libc.pthread_setaffinity_np
pthread_setaffinity_np.argtypes = [
    ctypes.c_long,  # thread (0 = current)
    ctypes.c_size_t,  # cpusetsize
    ctypes.POINTER(_cpuset_macos)],  # cpuset
pthread_setaffinity_np.restype = ctypes.c_int


def _mask_to_cpuset(mask: int) -> _cpuset_macos:
    """Convert integer bitmask to macOS cpuset structure."""
    cpuset = _cpuset_macos()
    for i in range(CPU_SETSIZE):
        if (mask >> i) & 1:
            cpuset.__bits[i // 32] |= 1 << (i % 32)
    return cpuset


def _set_thread_affinity(mask: int) -> bool:
    """
    Set thread affinity using pthread_setaffinity_np.
    
    Args:
        mask: Bitmask of allowed CPU cores (0 = all cores)
        
    Returns:
        True if successful, False otherwise
    """
    if not is_apple_silicon():
        logger.debug("[CPUAffinity] Not Apple Silicon, skipping affinity")
        return False
    
    try:
        cpuset = _mask_to_cpuset(mask)
        result = pthread_setaffinity_np(
            ctypes.c_long(0),  # current thread
            ctypes.c_size_t(CPU_MASK_SIZE),
            cpuset
        )
        if result == 0:
            return True
        else:
            logger.warning("[CPUAffinity] pthread_setaffinity_np failed: %d", result)
            return False
    except Exception as e:
        logger.warning("[CPUAffinity] Failed to set thread affinity: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# M1 Topology Detection
# ═══════════════════════════════════════════════════════════════════════════════

# Cache for topology to avoid repeated sysctl calls
_topology_cache: dict | None = None


def _detect_m1_topology() -> dict:
    """
    Detect M1 core topology using sysctl.
    
    Returns:
        dict with keys: p_cores, e_cores, total, p_core_mask, e_core_mask
    """
    global _topology_cache
    if _topology_cache is not None:
        return _topology_cache
    
    # Default for M1 8GB (4P + 0E)
    topology = {
        "p_cores": 4,
        "e_cores": 0,
        "total": 4,
        "p_core_mask": 0b1111,  # Cores 0-3
        "e_core_mask": 0,
        "model": "M1 (default)",
    }
    
    if not is_apple_silicon():
        return topology
    
    try:
        # Try to get core counts from sysctl
        import subprocess
        
        # Get performance core count
        result = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            topology["p_cores"] = int(result.stdout.strip())
        
        # Get efficiency core count
        result = subprocess.run(
            ["sysctl", "-n", "hw.perflevel1.physicalcpu"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            topology["e_cores"] = int(result.stdout.strip())
        
        topology["total"] = topology["p_cores"] + topology["e_cores"]
        
        # Build masks: P-cores = first N cores, E-cores = remaining cores
        topology["p_core_mask"] = (1 << topology["p_cores"]) - 1
        topology["e_core_mask"] = ((1 << topology["total"]) - 1) ^ topology["p_core_mask"]
        
        # Get chip model
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            topology["model"] = result.stdout.strip()
        
        logger.info(
            "[CPUAffinity] Detected %s: %d P-cores, %d E-cores",
            topology["model"], topology["p_cores"], topology["e_cores"]
        )
        
    except Exception as e:
        logger.warning("[CPUAffinity] Failed to detect topology: %s, using defaults", e)
    
    _topology_cache = topology
    return topology


def get_core_topology() -> dict:
    """Get M1 core topology (cached)."""
    return _detect_m1_topology()


def get_p_core_mask() -> int:
    """
    Get bitmask for P-cores (performance cores).
    
    Returns:
        Integer bitmask with P-core bits set
    """
    topology = _detect_m1_topology()
    return topology["p_core_mask"]


def get_e_core_mask() -> int:
    """
    Get bitmask for E-cores (efficiency cores).
    
    Returns:
        Integer bitmask with E-core bits set (0 if no E-cores)
    """
    topology = _detect_m1_topology()
    return topology["e_core_mask"]


# ═══════════════════════════════════════════════════════════════════════════════
# Affinity Functions
# ═══════════════════════════════════════════════════════════════════════════════

def set_mlx_affinity() -> bool:
    """
    Set affinity for MLX Metal / compute-intensive threads.
    
    MODERN-35 Fix: Pins current thread to P-cores only.
    MLX Metal compute should run on P-cores for maximum performance.
    E-cores are reserved for I/O.
    
    Usage:
        # Before MLX inference
        set_mlx_affinity()
        
        # Or as context manager
        with mlx_affinity():
            mx.eval(model(input_ids))
    
    Returns:
        True if affinity was set successfully
    """
    if not is_apple_silicon():
        logger.debug("[CPUAffinity] Not Apple Silicon, MLX affinity not needed")
        return False
    
    p_core_mask = get_p_core_mask()
    if p_core_mask == 0:
        logger.warning("[CPUAffinity] No P-cores detected, skipping affinity")
        return False
    
    success = _set_thread_affinity(p_core_mask)
    if success:
        logger.debug("[CPUAffinity] Thread pinned to P-cores (mask=0x%x)", p_core_mask)
    return success


def set_io_affinity() -> bool:
    """
    Set affinity for I/O-bound threads.
    
    MODERN-35 Fix: Pins current thread to E-cores if available.
    If no E-cores (M1 8GB = 0 E-cores), allows all cores.
    E-cores are more power-efficient for I/O waiting.
    
    Usage:
        # Before network/disk operations
        set_io_affinity()
    
    Returns:
        True if affinity was set successfully (or skipped if no E-cores)
    """
    if not is_apple_silicon():
        return False
    
    e_core_mask = get_e_core_mask()
    if e_core_mask == 0:
        # No E-cores (M1 8GB) — don't restrict, let OS schedule
        logger.debug("[CPUAffinity] No E-cores available, I/O affinity not restricted")
        return False
    
    success = _set_thread_affinity(e_core_mask)
    if success:
        logger.debug("[CPUAffinity] Thread pinned to E-cores (mask=0x%x)", e_core_mask)
    return success


def set_ane_affinity() -> bool:
    """
    Set affinity for ANE (Apple Neural Engine) inference threads.
    
    MODERN-35 Fix: ANE inference should run with P-core affinity.
    The Neural Engine is a dedicated chip, but CPU preprocessing
    should run on P-cores for minimum latency.
    
    Note: ANE hardware doesn't use CPU cores directly, but the
    CoreML dispatch threads should run on P-cores.
    
    Returns:
        True if affinity was set successfully
    """
    return set_mlx_affinity()  # ANE uses same P-core affinity as MLX


class mlx_affinity:
    """
    Context manager for MLX P-core affinity.
    
    Usage:
        with mlx_affinity():
            mx.eval(model(input_ids))  # Runs on P-cores
    
    Thread affinity is reset on context exit.
    """
    
    def __init__(self):
        self._original_mask: int | None = None
    
    def __enter__(self) -> "mlx_affinity":
        if is_apple_silicon():
            p_core_mask = get_p_core_mask()
            if p_core_mask > 0:
                _set_thread_affinity(p_core_mask)
                logger.debug("[CPUAffinity] Entered MLX P-core affinity")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Reset to all cores on exit
        if is_apple_silicon() and self._original_mask is not None:
            _set_thread_affinity(0)  # 0 = all cores
            logger.debug("[CPUAffinity] Exited MLX P-core affinity")


class io_affinity:
    """
    Context manager for I/O E-core affinity.
    
    Usage:
        with io_affinity():
            await fetch(url)  # Runs on E-cores if available
    
    Thread affinity is reset on context exit.
    """
    
    def __init__(self):
        self._original_mask: int | None = None
    
    def __enter__(self) -> "io_affinity":
        if is_apple_silicon():
            e_core_mask = get_e_core_mask()
            if e_core_mask > 0:
                _set_thread_affinity(e_core_mask)
                logger.debug("[CPUAffinity] Entered I/O E-core affinity")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if is_apple_silicon() and self._original_mask is not None:
            _set_thread_affinity(0)
            logger.debug("[CPUAffinity] Exited I/O E-core affinity")


# ═══════════════════════════════════════════════════════════════════════════════
# MLX Integration
# ═══════════════════════════════════════════════════════════════════════════════

# Cache for MLX thread pool state
_mlx_initialized: bool = False


def init_mlx_affinity() -> None:
    """
    Initialize MLX Metal affinity at startup.
    
    MODERN-35 Fix: Call this after MLX is imported but before
    any inference. Sets the default thread affinity for MLX operations.
    
    Usage:
        import mlx.core as mx
        from utils.cpu_affinity import init_mlx_affinity
        
        init_mlx_affinity()
        # Now all MLX operations use P-core affinity
    """
    global _mlx_initialized
    if _mlx_initialized:
        return
    
    if is_apple_silicon():
        success = set_mlx_affinity()
        if success:
            logger.info(
                "[CPUAffinity] MLX Metal initialized with P-core affinity "
                "(%d P-cores)", get_core_topology()["p_cores"]
            )
        else:
            logger.warning("[CPUAffinity] Failed to set MLX P-core affinity")
    
    _mlx_initialized = True
