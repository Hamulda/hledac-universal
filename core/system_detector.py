"""
System Detector - Runtime hardware capability detection for M1 Adaptive Defaults
================================================================================

Sprint: Issue 14 / F650M
Target: Replace hardcoded M1 8GB limits with runtime detection

RUNTIME DETECTION:
- platform.mac_ver() — macOS version, hardware model
- os.cpu_count() — CPU core count
- psutil.virtual_memory() — total/available RAM
- sysconfig.get_config_vars() — Python build flags
- MLX Metal detection — GPU availability

ADAPTIVE DEFAULTS by RAM tier:
- 8 GB  (M1 MacBook Air):  max_memory_mb=4096, concurrency=2
- 16 GB (M1/M2 Mac):       max_memory_mb=8192, concurrency=4
- 32 GB (M1/M2 Mac Pro):   max_memory_mb=16384, concurrency=8
- Other:                   max_memory_mb=6144, concurrency=3

INVARIANTS (always-on, no toggles):
- Fail-safe: any detection error returns safe defaults
- Bounded: O(1) detection, cached after first call
- No MLX import at module level (lazy)

Usage:
    from core.system_detector import SystemDetector, get_system_detector

    detector = get_system_detector()
    print(f"RAM tier: {detector.ram_tier}")        # "8gb" | "16gb" | "32gb" | "other"
    print(f"Max memory MB: {detector.max_memory_mb}")  # adaptive
    print(f"Max concurrent tools: {detector.max_concurrent_tools}")  # adaptive
    print(f"Is M1 8GB: {detector.is_m1_8gb}")      # True on M1 MacBook Air 8GB
"""
import os
import platform
import sys
import sysconfig
from dataclasses import dataclass, field
import msgspec
from functools import lru_cache
from typing import Literal
try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal
from core.psutil_shim import psutil_module as _psutil_mod

class HardwareCapabilities(msgspec.Struct, frozen=True, gc=False):
    """
    Immutable hardware capability snapshot.

    Frozen dataclass ensures hashability and prevents accidental mutation.
    Created once per process, cached via SystemDetector.
    """
    is_darwin: bool = False
    darwin_version: tuple[int, int, int] | None = None
    darwin_machine: str | None = None
    cpu_count_physical: int = 0
    cpu_count_logical: int = 0
    memory_total_bytes: int = 0
    memory_available_bytes: int = 0
    memory_total_gb: float = 0.0
    memory_available_gb: float = 0.0
    ram_tier: Literal['8gb', '16gb', '32gb', '64gb', 'other'] = 'other'
    python_build_flags: tuple[str, ...] = field(default_factory=tuple)
    has_metal: bool = False
    has_ane: bool = False
    is_m1_silicon: bool = False
    is_m1_8gb: bool = False
    # Sprint FXXX: Python 3.14 JIT detection (PEP 749)
    # PEP 749: JIT enabled by default in Python 3.14 if interpreter built with --with-jit
    is_jit_available: bool = False
    is_jit_active: bool = False
    jit_reason: str = ""

    @property
    def max_memory_mb(self) -> int:
        """
        Adaptive max_memory_mb based on RAM tier.

        Returns:
            - 8gb tier: 4096 MB
            - 16gb tier: 8192 MB
            - 32gb tier: 16384 MB
            - 64gb tier: 32768 MB
            - other: 6144 MB (safe middle ground)
        """
        tier_limits: dict[str, int] = {'8gb': 4096, '16gb': 8192, '32gb': 16384, '64gb': 32768, 'other': 6144}
        return tier_limits.get(self.ram_tier, 6144)

    @property
    def max_concurrent_tools(self) -> int:
        """
        Adaptive max_concurrent_tools based on RAM tier.

        Conservative for M1 8GB (2 tools), scales up for larger machines.

        Returns:
            - 8gb tier: 2 (M1 8GB safe)
            - 16gb tier: 4
            - 32gb tier: 8
            - 64gb tier: 12
            - other: 3
        """
        tier_concurrency: dict[str, int] = {'8gb': 2, '16gb': 4, '32gb': 8, '64gb': 12, 'other': 3}
        return tier_concurrency.get(self.ram_tier, 3)

    @property
    def fetch_concurrency(self) -> int:
        """
        Adaptive HTTP fetch concurrency based on available memory.

        F290: M1 8GB RAM budget allows 8 concurrent HTTP connections.
        Scales with available headroom.

        Returns:
            - < 2GB available: 3
            - 2-4GB available: 5
            - 4-8GB available: 8
            - > 8GB available: 12
        """
        available_gb = self.memory_available_gb
        if available_gb < 2:
            return 3
        elif available_gb < 4:
            return 5
        elif available_gb < 8:
            return 8
        else:
            return 12

class SystemDetector:
    """
    Runtime hardware capability detection.

    Single detection pass at init, results cached forever.
    Thread-safe (immutable result object).

    Usage:
        detector = SystemDetector()
        caps = detector.capabilities  # HardwareCapabilities instance
    """
    _cached_capabilities: HardwareCapabilities | None = None

    def __new__(cls) -> 'SystemDetector':
        """Singleton: return cached instance if already detected."""
        if cls._cached_capabilities is None:
            instance = super().__new__(cls)
            instance._detect()
            cls._cached_capabilities = instance._capabilities
        return instance

    def __init__(self) -> None:
        pass

    def _detect(self) -> None:
        """Run all hardware detection (called once per process)."""
        is_darwin = sys.platform == 'darwin'
        darwin_version: tuple[int, int, int] | None = None
        darwin_machine: str | None = None
        if is_darwin:
            try:
                ver = platform.mac_ver()
                if ver[0]:
                    version_str = ver[0].split('.')
                    parsed = [int(x) for x in version_str[:3]]
                    darwin_version = (parsed[0], parsed[1], parsed[2]) if len(parsed) >= 3 else None
                else:
                    darwin_version = None
                darwin_machine = ver[2] if len(ver) > 2 else None
            except Exception:
                darwin_version = None
                darwin_machine = None
        try:
            import os
            cpu_logical = os.cpu_count() or 0
            cpu_physical = cpu_logical
            try:
                psutil = _psutil_mod()
                cpu_physical = psutil.cpu_count(logical=False) or cpu_logical
            except Exception:
                pass
        except Exception:
            cpu_logical = 0
            cpu_physical = 0
        memory_total_bytes = 0
        memory_available_bytes = 0
        memory_total_gb = 0.0
        memory_available_gb = 0.0
        ram_tier: Literal['8gb', '16gb', '32gb', '64gb', 'other'] = 'other'
        try:
            psutil = _psutil_mod()
            vm = psutil.virtual_memory()
            memory_total_bytes = getattr(vm, 'total', 0)
            memory_available_bytes = getattr(vm, 'available', 0)
            memory_total_gb = memory_total_bytes / 1024 ** 3
            memory_available_gb = memory_available_bytes / 1024 ** 3
            if 7.5 <= memory_total_gb < 9:
                ram_tier = '8gb'
            elif 15 <= memory_total_gb < 18:
                ram_tier = '16gb'
            elif 30 <= memory_total_gb < 36:
                ram_tier = '32gb'
            elif 60 <= memory_total_gb < 72:
                ram_tier = '64gb'
            else:
                ram_tier = 'other'
        except Exception:
            pass
        python_build_flags: tuple[str, ...] = ()
        try:
            config = sysconfig.get_config_vars()
            flags = []
            for key, value in config.items():
                if key.endswith('FLAGS') and value:
                    flags.append(str(value))
            python_build_flags = tuple(flags)
        except Exception:
            pass
        has_metal = False
        has_ane = False
        is_m1_silicon = False
        is_m1_8gb = False

        # Sprint FXXX: Python 3.14 JIT detection (PEP 749)
        # PEP 749: JIT enabled by default in Python 3.14+ if interpreter built with --with-jit
        is_jit_available = False
        is_jit_active = False
        jit_reason = ""
        try:
            if hasattr(sys, 'jit'):
                is_jit_available = True
                # JIT is active if sys.flags.jit == 1
                try:
                    import sys
                    jit_flag = getattr(sys.flags, 'jit', 0)
                    is_jit_active = jit_flag == 1
                    jit_reason = f"sys.jit available, sys.flags.jit={jit_flag}"
                except Exception:
                    jit_reason = "sys.jit available, sys.flags.jit check failed"
            else:
                jit_reason = "sys.jit attribute not available (Python < 3.14 or built without --with-jit)"
        except Exception as e:
            jit_reason = f"JIT detection error: {e}"

        if is_darwin:
            try:
                import mlx.core as mx
                has_metal = mx.metal.is_available()
                has_ane = hasattr(mx.metal, 'get_ane_utilization')
                is_m1_silicon = darwin_machine is not None and 'arm' in darwin_machine.lower()
                is_m1_8gb = is_m1_silicon and ram_tier == '8gb'
            except ImportError:
                pass
            except Exception:
                pass
        self._capabilities = HardwareCapabilities(is_darwin=is_darwin, darwin_version=darwin_version, darwin_machine=darwin_machine, cpu_count_physical=cpu_physical, cpu_count_logical=cpu_logical, memory_total_bytes=memory_total_bytes, memory_available_bytes=memory_available_bytes, memory_total_gb=memory_total_gb, memory_available_gb=memory_available_gb, ram_tier=ram_tier, python_build_flags=python_build_flags, has_metal=has_metal, has_ane=has_ane, is_m1_silicon=is_m1_silicon, is_m1_8gb=is_m1_8gb, is_jit_available=is_jit_available, is_jit_active=is_jit_active, jit_reason=jit_reason)

    @property
    def capabilities(self) -> HardwareCapabilities:
        """Get cached hardware capabilities."""
        return self._capabilities

    @property
    def ram_tier(self) -> str:
        return self._capabilities.ram_tier

    @property
    def max_memory_mb(self) -> int:
        return self._capabilities.max_memory_mb

    @property
    def max_concurrent_tools(self) -> int:
        return self._capabilities.max_concurrent_tools

    @property
    def fetch_concurrency(self) -> int:
        return self._capabilities.fetch_concurrency

    @property
    def is_m1_8gb(self) -> bool:
        return self._capabilities.is_m1_8gb

@lru_cache(maxsize=1)
def get_system_detector() -> SystemDetector:
    """
    Get SystemDetector singleton.

    Cached via lru_cache — returns same instance on repeated calls.
    Detection runs only once per process.
    """
    return SystemDetector()

def get_hardware_capabilities() -> HardwareCapabilities:
    """
    Get hardware capabilities snapshot.

    Shorthand for get_system_detector().capabilities
    """
    return get_system_detector().capabilities