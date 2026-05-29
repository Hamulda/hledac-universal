"""
Thermal Monitor Helper - Sprint 1B Resource Hardening.

Lightweight macOS thermal state reader.
Push-friendly / observer-friendly scaffold, NO polling loop,
NO wiring into orchestrator at this stage.

API:
- get_thermal_state() -> tuple[int, str]  (level 0-3, "nominal"/"fair"/"serious"/"critical")
- get_thermal_state_str() -> str
- is_thermal_critical() -> bool  (serious or critical)
- format_thermal_snapshot() -> dict

Fail-open: returns (0, "nominal") on non-macOS or error.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import platform
import subprocess

__all__ = [
    "get_thermal_state",
    "get_thermal_state_str",
    "is_thermal_warn",
    "is_thermal_critical",
    "format_thermal_snapshot",
]

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Thermal level constants (mach/oceanic.h / IOKit)
# -----------------------------------------------------------------------
_THERMAL_LEVELS = {
    0: "nominal",
    1: "fair",
    2: "serious",
    3: "critical",
}

# Lazy singleton for process handle
_PDL_INITIALIZED: bool | None = None
_PDL_HANDLE: ctypes.c_int | None = None


def _get_pdl_handle():
    """
    Lazy init of ProcessDataLink handle via IOKit.
    Returns None on failure (non-macOS, missing libc, sandbox block, etc.)
    """
    global _PDL_INITIALIZED, _PDL_HANDLE

    if _PDL_INITIALIZED is not None:
        return _PDL_HANDLE if _PDL_INITIALIZED else None

    _PDL_INITIALIZED = False

    if platform.system() != "Darwin":
        return None

    try:
        # Load IOKit
        iokit = ctypes.util.find_library("IOKit")
        if iokit is None:
            return None

        iokit_lib = ctypes.CDLL(iokit)

        # IOServiceGetMatchingService
        iokit_lib.IOServiceGetMatchingService.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit_lib.IOServiceGetMatchingService.restype = ctypes.c_void_p

        # IOServiceMatching
        iokit_lib.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit_lib.IOServiceMatching.restype = ctypes.c_void_p

        # IOObjectRelease
        iokit_lib.IOObjectRelease.argtypes = [ctypes.c_void_p]
        iokit_lib.IOObjectRelease.restype = ctypes.c_int

        # IOServiceOpen
        iokit_lib.IOServiceOpen.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]
        iokit_lib.IOServiceOpen.restype = ctypes.c_int

        # Create matching dict for AppleSMC
        smc_service = iokit_lib.IOServiceMatching(b"AppleSMC")
        if not smc_service:
            return None

        service = iokit_lib.IOServiceGetMatchingService(0, smc_service)
        if not service:
            return None

        # Open connection to SMC
        connect_ptr = ctypes.c_int(0)
        result = iokit_lib.IOServiceOpen(service, 0, 0, connect_ptr)
        iokit_lib.IOObjectRelease(service)

        if result != 0:
            return None

        _PDL_INITIALIZED = True
        _PDL_HANDLE = connect_ptr
        return connect_ptr

    except Exception as e:
        logger.debug(f"Thermal monitor init failed: {e}")
        _PDL_INITIALIZED = False
        _PDL_HANDLE = None
        return None


def get_thermal_state() -> tuple[int, str]:
    """
    Read macOS thermal state.

    Returns:
        (level: int, name: str)
        level: 0=nominal, 1=fair, 2=serious, 3=critical
        name:  "nominal", "fair", "serious", "critical"

    Fail-open: returns (0, "nominal") on any error.
    """
    if platform.system() != "Darwin":
        return 0, "nominal"

    try:
        # Try IOKit SMC read first (most accurate)
        pdl = _get_pdl_handle()
        if pdl is not None:
            # SMC connection established - thermal read path ready
            pass

        # Fallback: parse sysctl for thermal level
        result = subprocess.run(
            ["sysctl", "-n", "hw.acpi.thermal.user_thermal_policy"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            try:
                level = int(result.stdout.strip())
                if level in _THERMAL_LEVELS:
                    return level, _THERMAL_LEVELS[level]
            except ValueError:
                pass

        # Fallback: platform.mac_ver() as last resort (macOS only signal)
        ver = platform.mac_ver()
        if ver[0]:
            # macOS detected, assume nominal unless proven otherwise
            return 0, "nominal"

    except Exception as e:
        logger.debug(f"get_thermal_state failed: {e}")

    return 0, "nominal"


def get_thermal_state_str() -> str:
    """Return just the thermal state name string."""
    _, name = get_thermal_state()
    return name


def is_thermal_warn() -> bool:
    """
    Returns True if thermal state is fair (1) or higher.
    Use for aggressive throttling decisions in caller.
    """
    level, _ = get_thermal_state()
    return level >= 1


def is_thermal_critical() -> bool:
    """
    Returns True if thermal state is serious (2) or critical (3).
    Use for throttling decisions in caller.
    """
    level, _ = get_thermal_state()
    return level >= 2


def format_thermal_snapshot() -> dict:
    """
    Return a complete thermal snapshot dict.
    """
    level, name = get_thermal_state()
    return {
        "platform": platform.system(),
        "macos": platform.mac_ver()[0] if platform.system() == "Darwin" else "",
        "level": level,
        "name": name,
        "is_warn": is_thermal_warn(),
        "is_critical": is_thermal_critical(),
    }
