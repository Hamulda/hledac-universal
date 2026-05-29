"""
PerformanceMonitor - Sledování výkonu z M1MasterOrchestrator

Funkce:
- Sledování rychlosti (tokens/sec, queries/sec)
- Speedup tracking oproti baseline
- Quality validation
- Memory profiling
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Metriky výkonu"""
    generation_count: int = 0
    total_tokens: int = 0
    total_time: float = 0.0
    avg_speedup: float = 0.0
    quality_scores: list[float] = field(default_factory=list)

    def record_generation(
        self,
        tokens: int,
        duration: float,
        baseline_time: float,
        quality_score: float = 1.0
    ) -> None:
        """Zaznamenat generování"""
        self.generation_count += 1
        self.total_tokens += tokens
        self.total_time += duration

        # Vypočítat speedup
        if duration > 0:
            speedup = baseline_time / duration
            # Update running average
            self.avg_speedup = (
                (self.avg_speedup * (self.generation_count - 1) + speedup)
                / self.generation_count
            )

        self.quality_scores.append(quality_score)

    def get_stats(self) -> dict[str, Any]:
        """Získat statistiky"""
        avg_quality = sum(self.quality_scores) / len(self.quality_scores) if self.quality_scores else 0

        return {
            "generations": self.generation_count,
            "total_tokens": self.total_tokens,
            "total_time_sec": self.total_time,
            "avg_tokens_per_sec": self.total_tokens / self.total_time if self.total_time > 0 else 0,
            "avg_speedup": self.avg_speedup,
            "avg_quality": avg_quality,
        }


class PerformanceMonitor:
    """
    Monitor výkonu pro UniversalResearchOrchestrator.

    Features:
    - Speedup tracking
    - Quality validation
    - Memory profiling
    - Baseline estimation
    """

    def __init__(self):
        self.metrics = PerformanceMetrics()
        self._baseline_stats = {}

    def start_timer(self) -> float:
        """Start časování"""
        return time.time()

    def record(
        self,
        tokens: int,
        start_time: float,
        quality_score: float = 1.0
    ) -> dict[str, Any]:
        """
        Zaznamenat generování.

        Args:
            tokens: Počet tokenů
            start_time: Čas začátku (z start_timer)
            quality_score: Skóre kvality (0-1)

        Returns:
            Statistiky
        """
        duration = time.time() - start_time
        baseline_time = self._estimate_baseline_time(tokens)

        self.metrics.record_generation(tokens, duration, baseline_time, quality_score)

        speedup = baseline_time / duration if duration > 0 else 0

        logger.info(f"Generation complete: {duration:.2f}s, speedup: {speedup:.1f}×")

        return {
            "duration": duration,
            "speedup": speedup,
            "tokens_per_sec": tokens / duration if duration > 0 else 0,
        }

    def _estimate_baseline_time(self, tokens: int) -> float:
        """
        Odhadnout baseline čas (bez optimalizací).

        Args:
            tokens: Počet tokenů

        Returns:
            Odhadovaný čas v sekundách
        """
        # Baseline: ~10 tokens/sec bez optimalizací
        return tokens / 10.0

    def get_stats(self) -> dict[str, Any]:
        """Získat statistiky"""
        return self.metrics.get_stats()

    def reset(self) -> None:
        """Reset metrik"""
        self.metrics = PerformanceMetrics()


class QualityValidator:
    """
    Validátor kvality výstupu.

    Zajišťuje 100% identitu výstupu (nebo ji měří).
    """

    def __init__(self):
        self._reference_outputs = {}

    def check_output_quality(
        self,
        output: str,
        reference: str = None
    ) -> dict[str, Any]:
        """
        Zkontrolovat kvalitu výstupu.

        Args:
            output: Vygenerovaný výstup
            reference: Referenční výstup (pro porovnání)

        Returns:
            Výsledky validace
        """
        metrics = {
            "length": len(output),
            "tokens": len(output.split()),
            "score": 1.0,  # Default: perfect
        }

        if reference:
            # Porovnat s referencí
            similarity = self._calculate_similarity(output, reference)
            metrics["similarity"] = similarity
            metrics["score"] = similarity

        # Heuristiky kvality
        if len(output) < 10:
            metrics["score"] *= 0.5  # Příliš krátké

        return metrics

    def _calculate_similarity(self, a: str, b: str) -> float:
        """Vypočítat podobnost dvou textů"""
        # Jednoduchá Jaccard podobnost
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())

        if not set_a and not set_b:
            return 1.0

        intersection = len(set_a & set_b)
        union = len(set_a | set_b)

        return intersection / union if union > 0 else 0.0



# =============================================================================
# SYSTEM MONITOR (Integrated from hledac/utils/systemcontext.py)
# =============================================================================

from enum import Enum

import psutil


class ThermalState(Enum):
    """M1 thermal states."""
    COOL = "cool"
    NORMAL = "normal"
    WARM = "warm"
    HOT = "hot"
    CRITICAL = "critical"


class MemoryPressure(Enum):
    """Memory pressure levels for 8GB M1."""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SystemMetrics:
    """Current system metrics."""
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    thermal_state: ThermalState
    memory_pressure: MemoryPressure
    battery_percent: float | None = None
    is_charging: bool | None = None
    timestamp: float = 0.0


class SystemMonitor:
    """
    System monitor for M1 optimization.

    Tracks system metrics and provides callbacks for state changes.
    Optimized for M1 MacBook with 8GB RAM.
    """

    def __init__(self, sample_interval: float = 1.0):
        """
        Initialize system monitor.

        Args:
            sample_interval: How often to sample metrics (seconds)
        """
        self.sample_interval = sample_interval
        self._metrics = SystemMetrics(
            cpu_percent=0.0,
            memory_percent=0.0,
            memory_available_mb=0.0,
            thermal_state=ThermalState.NORMAL,
            memory_pressure=MemoryPressure.LOW,
            timestamp=time.time()
        )
        self._callbacks: dict[str, Any] = {}
        self._monitoring_task: asyncio.Task | None = None
        self._running = False

    async def start_monitoring(self) -> None:
        """Start background monitoring."""
        if self._running:
            return

        self._running = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info("System monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("System monitoring stopped")

    async def _monitoring_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                await self._update_metrics()
                await asyncio.sleep(self.sample_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.sample_interval)

    async def _update_metrics(self) -> None:
        """Update system metrics."""
        try:
            # Get CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)

            # Get memory info
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_available_mb = memory.available / (1024 * 1024)

            # Determine memory pressure
            memory_pressure = self._get_memory_pressure(memory_percent, memory_available_mb)

            # Determine thermal state (simulated based on CPU)
            thermal_state = self._get_thermal_state(cpu_percent)

            # Get battery info if available
            battery_percent = None
            is_charging = None
            try:
                battery = psutil.sensors_battery()
                if battery:
                    battery_percent = battery.percent
                    is_charging = battery.power_plugged
            except Exception:
                pass

            # Create new metrics
            new_metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_available_mb=memory_available_mb,
                thermal_state=thermal_state,
                memory_pressure=memory_pressure,
                battery_percent=battery_percent,
                is_charging=is_charging,
                timestamp=time.time()
            )

            # Check for state changes and trigger callbacks
            await self._check_state_changes(self._metrics, new_metrics)

            self._metrics = new_metrics

        except Exception as e:
            logger.error(f"Error updating metrics: {e}")

    def _get_memory_pressure(self, memory_percent: float, memory_available_mb: float) -> MemoryPressure:
        """Determine memory pressure level."""
        if memory_percent > 95 or memory_available_mb < 200:
            return MemoryPressure.CRITICAL
        elif memory_percent > 85 or memory_available_mb < 500:
            return MemoryPressure.HIGH
        elif memory_percent > 70 or memory_available_mb < 1000:
            return MemoryPressure.MODERATE
        else:
            return MemoryPressure.LOW

    def _get_thermal_state(self, cpu_percent: float) -> ThermalState:
        """Determine thermal state from CPU usage."""
        if cpu_percent > 90:
            return ThermalState.CRITICAL
        elif cpu_percent > 70:
            return ThermalState.HOT
        elif cpu_percent > 50:
            return ThermalState.WARM
        elif cpu_percent > 20:
            return ThermalState.NORMAL
        else:
            return ThermalState.COOL

    async def _check_state_changes(self, old_metrics: SystemMetrics, new_metrics: SystemMetrics) -> None:
        """Check for state changes and trigger callbacks."""
        if old_metrics.thermal_state != new_metrics.thermal_state:
            if 'thermal_change' in self._callbacks:
                self._callbacks['thermal_change'](new_metrics)

        if old_metrics.memory_pressure != new_metrics.memory_pressure:
            if 'memory_change' in self._callbacks:
                self._callbacks['memory_change'](new_metrics)

        if (new_metrics.thermal_state == ThermalState.CRITICAL or
            new_metrics.memory_pressure == MemoryPressure.CRITICAL):
            if 'critical_state' in self._callbacks:
                self._callbacks['critical_state'](new_metrics)

    def register_callback(self, event: str, callback: Any) -> None:
        """Register callback for system events."""
        self._callbacks[event] = callback

    def unregister_callback(self, event: str) -> None:
        """Unregister callback."""
        if event in self._callbacks:
            del self._callbacks[event]

    def get_metrics(self) -> SystemMetrics:
        """Get current system metrics."""
        return self._metrics

    def should_throttle(self) -> bool:
        """Check if processing should be throttled."""
        return (
            self._metrics.thermal_state in [ThermalState.HOT, ThermalState.CRITICAL] or
            self._metrics.memory_pressure in [MemoryPressure.HIGH, MemoryPressure.CRITICAL]
        )

    def get_recommendations(self) -> list[str]:
        """Get performance recommendations based on current state."""
        recommendations = []

        if self._metrics.thermal_state == ThermalState.CRITICAL:
            recommendations.append("CRITICAL: Stop intensive operations immediately")
        elif self._metrics.thermal_state == ThermalState.HOT:
            recommendations.append("WARNING: Reduce processing load to cool down")

        if self._metrics.memory_pressure == MemoryPressure.CRITICAL:
            recommendations.append("CRITICAL: Free memory immediately or risk OOM")
        elif self._metrics.memory_pressure == MemoryPressure.HIGH:
            recommendations.append("WARNING: Reduce memory usage")

        return recommendations

    def get_snapshot(self) -> dict[str, Any]:
        """
        Get a snapshot of current system metrics for flow tracing.

        Sprint 8C1: Returns a dict suitable for trace metadata.

        Returns:
            Dict with rss_mb, cpu_percent, memory_percent, thermal_state,
            memory_pressure, and optional event_loop_lag_estimate.
        """
        m = self._metrics
        snapshot: dict[str, Any] = {
            "rss_mb": 0.0,
            "cpu_percent": m.cpu_percent,
            "memory_percent": m.memory_percent,
            "thermal_state": m.thermal_state.value if m.thermal_state else "unknown",
            "memory_pressure": m.memory_pressure.value if m.memory_pressure else "unknown",
        }

        # Get RSS if psutil available
        try:
            import psutil
            process = psutil.Process()
            snapshot["rss_mb"] = process.memory_info().rss / (1024 * 1024)
        except Exception:
            pass

        # Estimate event loop lag if we can
        try:
            asyncio.get_running_loop()
            # Use clock_time provides a cheap lag estimate
            snapshot["event_loop_lag_ms"] = 0.0  # Placeholder - actual lag measurement requires profiler
        except Exception:
            pass

        return snapshot


class FlowTraceSnapshotEmitter:
    """
    Optional periodic snapshot emitter for flow tracing integration.

    Sprint 8C1: When GHOST_FLOW_TRACE=1, periodically emits system snapshots
    to the flow trace stream. Lightweight - only active when tracing is enabled.
    """

    def __init__(self, monitor: SystemMonitor, interval: float = 5.0):
        self._monitor = monitor
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start periodic snapshot emission."""
        if self._running:
            return

        # Only start if tracing is enabled
        try:
            from ..utils.flow_trace import is_enabled
            if not is_enabled():
                return
        except Exception:
            return

        self._running = True
        self._task = asyncio.create_task(self._snapshot_loop())

    async def stop(self) -> None:
        """Stop periodic snapshot emission."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _snapshot_loop(self) -> None:
        """Background loop that emits snapshots."""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                snapshot = self._monitor.get_snapshot()

                # Emit as trace event
                try:
                    from ..utils.flow_trace import trace_event
                    trace_event(
                        component="performance_monitor",
                        stage="system_snapshot",
                        event_type="periodic_snapshot",
                        status="ok",
                        metadata=snapshot,
                    )
                except Exception:
                    pass  # Fail-open
            except asyncio.CancelledError:
                break
            except Exception:
                pass


# Update exports
__all__ = [
    'PerformanceMetrics',
    'PerformanceMonitor',
    'QualityValidator',
    # NEW from systemcontext:
    'ThermalState',
    'MemoryPressure',
    'SystemMetrics',
    'SystemMonitor',
    # Sprint 8C1:
    'FlowTraceSnapshotEmitter',
]
