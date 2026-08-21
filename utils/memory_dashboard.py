"""
Unified Memory Monitor - kombinované sledování systémové a GPU paměti.

Sprint 81: Core Stability & Memory Safety


- UnifiedMemorySnapshot - dataclass pro kombinovaný memory snapshot
- UnifiedMemoryMonitor - třída pro sledování unified memory na M1
"""

import logging
from typing import Any

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)
import platform

IS_DARWIN = platform.system() == "Darwin"
PSUTIL_AVAILABLE = False
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)


# Lazy accessor for mlx.core — uses centralized get_mx() from SSOT
def _get_mx() -> Any | None:
    """Lazily cached mlx.core module reference. Returns None if unavailable."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core

    return _get_mx_from_core()


class UnifiedMemorySnapshot(Struct, frozen=True):
    """
    Kombinovaný snapshot systémové a GPU (Metal) paměti.

    Sprint 81: Unified memory monitoring pro M1 8GB.
    """

    sys_total_gb: float
    sys_available_gb: float
    sys_used_gb: float
    sys_percent: float
    metal_active_gb: float | None = None
    metal_peak_gb: float | None = None
    metal_cache_gb: float | None = None
    pressure: float = 0.0
    metal_available_gb: float | None = None

    def __post_init__(self) -> None:
        """Vypočítat derived metriky."""
        total_available = self.sys_available_gb
        if self.metal_cache_gb is not None:
            total_available += self.metal_cache_gb
        self.metal_available_gb = total_available
        if self.sys_total_gb > 0:
            self.pressure = 1.0 - self.sys_available_gb / self.sys_total_gb

    @property
    def is_critical(self) -> bool:
        """Kritický stav - méně než 1GB dostupné."""
        return self.sys_available_gb < 1.0

    @property
    def is_warning(self) -> bool:
        """Varovný stav - méně než 2GB dostupné."""
        return self.sys_available_gb < 2.0


class UnifiedMemoryMonitor:
    """
    Monitor pro kombinované sledování systémové a Metal paměti.

    Použití:
        monitor = UnifiedMemoryMonitor()
        snapshot = monitor.snapshot()
        print(f"Available: {snapshot.sys_available_gb:.2f}GB")

    Sprint 81: Nahrazuje fragmentované memory monitoring v různých částech kódu.
    """

    __slots__ = ("_interval", "_last_snapshot")

    def __init__(self, interval: float = 1.0) -> None:
        """
        Args:
            interval: Interval mezi měřeními (pro budoucí EMA výpočty)
        """
        self._interval = interval
        self._last_snapshot: UnifiedMemorySnapshot | None = None

    def snapshot(self) -> UnifiedMemorySnapshot:
        """
        Získat aktuální snapshot paměti.

        Returns:
            UnifiedMemorySnapshot s aktuálními hodnotami
        """
        if PSUTIL_AVAILABLE:
            vm = psutil.virtual_memory()
            sys_total_gb = vm.total / 1024**3
            sys_available_gb = vm.available / 1024**3
            sys_used_gb = vm.used / 1024**3
            sys_percent = vm.percent
        else:
            sys_total_gb = 0.0
            sys_available_gb = 0.0
            sys_used_gb = 0.0
            sys_percent = 0.0
        metal_active_gb = None
        metal_peak_gb = None
        metal_cache_gb = None
        mx = _get_mx()
        if mx is not None and IS_DARWIN:
            try:
                if hasattr(mx.metal, "get_active_memory"):
                    metal_active_gb = mx.get_active_memory() / 1024**3
            except Exception:  # noqa: BLE001
                pass
            try:
                if hasattr(mx.metal, "get_peak_memory"):
                    metal_peak_gb = mx.get_peak_memory() / 1024**3
            except Exception:  # noqa: BLE001
                pass
            try:
                if hasattr(mx.metal, "get_cache_memory"):
                    metal_cache_gb = mx.get_cache_memory() / 1024**3
            except Exception:  # noqa: BLE001
                pass
        snapshot = UnifiedMemorySnapshot(
            sys_total_gb=sys_total_gb,
            sys_available_gb=sys_available_gb,
            sys_used_gb=sys_used_gb,
            sys_percent=sys_percent,
            metal_active_gb=metal_active_gb,
            metal_peak_gb=metal_peak_gb,
            metal_cache_gb=metal_cache_gb,
        )
        self._last_snapshot = snapshot
        return snapshot

    def get_pressure_level(self) -> str:
        """
        Získat úroveň memory pressure jako string.

        Returns:
            "critical" | "warning" | "normal" | "healthy"
        """
        snap = self.snapshot()
        if snap.sys_available_gb < 1.0:
            return "critical"
        elif snap.sys_available_gb < 2.0:
            return "warning"
        elif snap.sys_percent > 80:
            return "normal"
        else:
            return "healthy"

    def should_emergency_brake(self, critical_gb: float = 1.0, metal_peak_gb: float = 6.0) -> bool:
        """
        Určit, zda by měl být aktivován emergency brake.

        Args:
            critical_gb: Kritická hranice dostupné RAM (GB)
            metal_peak_gb: Kritická hranice peak Metal paměti (GB)

        Returns:
            True pokud by měl být aktivován emergency brake
        """
        snap = self.snapshot()
        if snap.sys_available_gb < critical_gb:
            return True
        if snap.metal_peak_gb is not None and snap.metal_peak_gb > metal_peak_gb:
            return True
        return False

    def get_summary(self) -> str:
        """
        Získat human-readable summary paměťového stavu.

        Returns:
            Formátovaný string s paměťovými statistikami
        """
        snap = self.snapshot()
        lines = [
            f"Memory: {snap.sys_available_gb:.2f}GB / {snap.sys_total_gb:.2f}GB available ({snap.sys_percent:.1f}% used)"
        ]
        if snap.metal_active_gb is not None:
            lines.append(f"Metal:  {snap.metal_active_gb:.2f}GB active")
        if snap.metal_peak_gb is not None:
            lines.append(f"Peak:   {snap.metal_peak_gb:.2f}GB peak")
        if snap.metal_cache_gb is not None:
            lines.append(f"Cache:  {snap.metal_cache_gb:.2f}GB cached")
        pressure = self.get_pressure_level()
        lines.append(f"Status: {pressure.upper()}")
        return "\n".join(lines)


def get_unified_snapshot() -> UnifiedMemorySnapshot:
    """
    Convenience funkce pro rychlý přístup k memory snapshotu.

    Returns:
        UnifiedMemorySnapshot s aktuálními hodnotami

    Example:
        snap = get_unified_snapshot()
        if snap.is_critical:
            logger.critical("Memory critical!")
    """
    return UnifiedMemoryMonitor().snapshot()
