"""
SystemMonitor - System monitoring pro UniversalResearchOrchestrator

CONTAINMENT METADATA (Sprint perf_c):
- ROLE: Bounded health observation surface (sync check_health model)
- IS NOT: Canonical system monitor, execution authority, scheduler, or control surface
- AUTHORITY: Read-only health checks via psutil; callbacks for notification only
- DUPLICATE NAME: Differs from utils/performance_monitor.SystemMonitor
  (which is async with ThermalState/MemoryPressure/flow tracing)
- NO DAEMON: No background monitoring loop; no asyncio.create_task regrowth
- M1 Air 8GB: Lightweight psutil-only, no eager init, no heavy deps

Monitoruje:
- CPU usage
- Memory usage
- Thermal status (M1)
- System health
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

import psutil

logger = logging.getLogger(__name__)


class SystemState(Enum):
    """Stavy systému"""
    HEALTHY = "healthy"
    MEMORY_PRESSURE = "memory_pressure"
    THERMAL_THROTTLING = "thermal_throttling"
    DEGRADED = "degraded"
    RECOVERY = "recovery"


class SystemMonitor:
    """
    Monitor systému pro M1.

    Features:
    - CPU/Memory monitoring
    - Thermal monitoring
    - State transitions
    - Callback system
    """

    def __init__(
        self,
        memory_threshold: float = 5500,  # MB
        thermal_threshold: float = 85,    # °C
    ):
        self.memory_threshold = memory_threshold
        self.thermal_threshold = thermal_threshold

        self._state = SystemState.HEALTHY
        self._callbacks: list[Callable] = []

    def get_state(self) -> SystemState:
        """Získat aktuální stav"""
        return self._state

    def check_health(self) -> SystemState:
        """
        Zkontrolovat zdraví systému.

        Returns:
            Aktuální stav
        """
        try:
            # Memory check
            memory = psutil.virtual_memory()
            used_mb = memory.used / (1024 * 1024)

            if used_mb > self.memory_threshold:
                new_state = SystemState.MEMORY_PRESSURE
            else:
                new_state = SystemState.HEALTHY

            # Thermal check (na M1)
            try:
                # pouze pokud je dostupné
                temps = psutil.sensors_temperatures()
                if temps:
                    for _name, entries in temps.items():
                        for entry in entries:
                            if entry.current > self.thermal_threshold:
                                new_state = SystemState.THERMAL_THROTTLING
                                break
            except Exception:
                pass

            # State transition
            if new_state != self._state:
                self._transition_state(new_state)

            return self._state

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return SystemState.DEGRADED

    def _transition_state(self, new_state: SystemState):
        """Přejít do nového stavu"""
        old_state = self._state
        self._state = new_state

        logger.warning(f"System state transition: {old_state.value} → {new_state.value}")

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def on_state_change(self, callback: Callable):
        """Registrovat callback na změnu stavu"""
        self._callbacks.append(callback)

    def get_stats(self) -> dict:
        """Získat statistiky systému"""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()

            stats = {
                "state": self._state.value,
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_mb": memory.used / (1024 * 1024),
                "memory_available_mb": memory.available / (1024 * 1024),
            }

            # Thermal
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    stats["temperatures"] = {
                        name: [e.current for e in entries]
                        for name, entries in temps.items()
                    }
            except Exception:
                pass

            return stats

        except Exception as e:
            return {"error": str(e)}
