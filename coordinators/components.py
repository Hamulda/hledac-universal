"""
Coordinator Components — Single Responsibility Composition
======================================================





Extracts three concerns from UniversalCoordinator into isolated components:
- OperationTracker: operation lifecycle tracking
- LoadFactorCalculator: capacity and load management
- MemoryPressureMonitor: M1 memory monitoring

Usage:
    from hledac.universal.coordinators.components import (
        OperationTracker,
        LoadFactorCalculator,
        MemoryPressureMonitor,
    )

    tracker = OperationTracker(name='fetch', max_concurrent=10)
    load = LoadFactorCalculator(tracker)
    memory = MemoryPressureMonitor()

    # Use via composition
    coordinator = SomeCoordinator(
        _tracker=tracker,
        _load=load,
        _memory=memory,
    )
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.coordinators.enums import MemoryPressureLevel

# =============================================================================
# Operation Tracker
# =============================================================================

@dataclass(slots=True)
class OperationTracker:
    """
    Operation lifecycle tracking.

    Tracks active operations and maintains operation history
    for debugging and telemetry.
    """
    name: str
    max_concurrent: int
    _active: dict[str, dict[str, Any]] = field(default_factory=dict)
    _history: OrderedDict[str, dict[str, Any]] = field(
        default_factory=lambda: OrderedDict()
    )
    _counter: int = field(default=0)
    _max_history: int = field(default=100)

    def track(self, operation_id: str, data: dict[str, Any]) -> None:
        """Track a new active operation."""
        self._active[operation_id] = {
            **data,
            'start_time': time.time(),
            'coordinator': self.name,
        }

    def untrack(self, operation_id: str) -> dict[str, Any] | None:
        """Remove operation from active tracking and add to history."""
        if operation_id not in self._active:
            return None

        op_data = self._active.pop(operation_id)
        op_data['end_time'] = time.time()
        self._history[operation_id] = op_data

        # Trim history if needed
        while len(self._history) > self._max_history:
            self._history.popitem(last=False)

        return op_data

    def generate_operation_id(self) -> str:
        """Generate unique operation ID with coordinator prefix."""
        self._counter += 1
        timestamp = int(time.time())
        return f'{self.name}_{timestamp}_{self._counter:04d}'

    @property
    def active_count(self) -> int:
        """Number of active operations."""
        return len(self._active)

    @property
    def active_operations(self) -> list[str]:
        """List of active operation IDs."""
        return list(self._active.keys())

    def get_status(self, operation_id: str) -> dict[str, Any] | None:
        """Get status of specific operation."""
        if operation_id in self._active:
            data = self._active[operation_id]
            return {
                'status': 'active',
                'elapsed': time.time() - data['start_time'],
                **data
            }
        elif operation_id in self._history:
            data = self._history[operation_id]
            return {
                'status': 'completed',
                'duration': data['end_time'] - data['start_time'],
                **data
            }
        return None


# =============================================================================
# Load Factor Calculator
# =============================================================================

@dataclass(slots=True)
class LoadFactorCalculator:
    """
    Load factor calculation for capacity management.

    Considers:
    - Active operation count vs max concurrent
    - Memory pressure multiplier (for M1 optimization)
    """
    _tracker: OperationTracker
    _thresholds: dict[int, float] = field(default_factory=lambda: {
        10: 1.0, 9: 0.95, 8: 0.9, 7: 0.85, 6: 0.8,
        5: 0.75, 4: 0.7, 3: 0.65, 2: 0.6, 1: 0.5
    })
    _memory_multiplier: float = field(default=1.0)

    def set_memory_multiplier(self, multiplier: float) -> None:
        """Update memory pressure multiplier."""
        self._memory_multiplier = max(1.0, multiplier)

    def get_load_factor(self) -> float:
        """
        Calculate current load factor (0.0 = idle, 1.0 = fully loaded).
        """
        active_load = self._tracker.active_count / self._tracker.max_concurrent
        return min(active_load * self._memory_multiplier, 1.0)

    def can_accept(self, priority: int = 5) -> bool:
        """
        Check if can accept new operation based on priority.

        Higher priority operations can be accepted even at high load.
        """
        if priority >= 9:
            return True
        load = self.get_load_factor()
        threshold = self._thresholds.get(priority, 0.75)
        return load < threshold

    def get_capacity_info(self) -> dict[str, Any]:
        """Get detailed capacity information."""
        return {
            'max_concurrent': self._tracker.max_concurrent,
            'active_operations': self._tracker.active_count,
            'available_slots': self._tracker.max_concurrent - self._tracker.active_count,
            'load_factor': self.get_load_factor(),
            'memory_multiplier': self._memory_multiplier,
            'can_accept_normal': self.can_accept(priority=5),
            'can_accept_critical': self.can_accept(priority=10),
        }


# =============================================================================
# Memory Pressure Monitor
# =============================================================================

@dataclass(slots=True)
class MemoryPressureMonitor:
    """
    M1 memory pressure monitoring.

    Tracks memory pressure levels and provides thresholds
    for memory-aware operation scheduling.
    """
    _current_level: MemoryPressureLevel = field(default=MemoryPressureLevel.NORMAL)
    _thresholds: dict[MemoryPressureLevel, float] = field(default_factory=lambda: {
        MemoryPressureLevel.ELEVATED: 0.75,
        MemoryPressureLevel.HIGH: 0.85,
        MemoryPressureLevel.CRITICAL: 0.95,
    })

    def check(self, memory_usage_ratio: float) -> MemoryPressureLevel:
        """
        Determine memory pressure level from usage ratio.

        Args:
            memory_usage_ratio: Current memory usage (0.0-1.0)

        Returns:
            MemoryPressureLevel
        """
        if memory_usage_ratio >= self._thresholds[MemoryPressureLevel.CRITICAL]:
            return MemoryPressureLevel.CRITICAL
        elif memory_usage_ratio >= self._thresholds[MemoryPressureLevel.HIGH]:
            return MemoryPressureLevel.HIGH
        elif memory_usage_ratio >= self._thresholds[MemoryPressureLevel.ELEVATED]:
            return MemoryPressureLevel.ELEVATED
        return MemoryPressureLevel.NORMAL

    def update(self, level: MemoryPressureLevel) -> None:
        """Update current memory pressure level."""
        if self._current_level != level:
            self._current_level = level

    @property
    def current_level(self) -> MemoryPressureLevel:
        """Get current memory pressure level."""
        return self._current_level

    def get_multiplier(self) -> float:
        """Get load factor multiplier based on pressure level."""
        multipliers = {
            MemoryPressureLevel.NORMAL: 1.0,
            MemoryPressureLevel.ELEVATED: 1.2,
            MemoryPressureLevel.HIGH: 1.5,
            MemoryPressureLevel.CRITICAL: 2.0,
        }
        return multipliers.get(self._current_level, 1.0)


# =============================================================================
# Null Implementations (for testing)
# =============================================================================

class NullOperationTracker:
    """No-op operation tracker for testing."""
    name: str = "null"
    max_concurrent: int = 0

    def track(self, _op_id: str, _data: dict[str, Any]) -> None:
        pass

    def untrack(self, _op_id: str) -> dict[str, Any] | None:
        return None

    def generate_operation_id(self) -> str:
        return "null_0"

    @property
    def active_count(self) -> int:
        return 0

    @property
    def active_operations(self) -> list[str]:
        return []

    def get_status(self, _operation_id: str) -> dict[str, Any] | None:
        return None


class NullLoadFactorCalculator:
    """No-op load factor calculator for testing."""

    def set_memory_multiplier(self, multiplier: float) -> None:
        pass

    def get_load_factor(self) -> float:
        return 0.0

    def can_accept(self, _priority: int = 5) -> bool:
        return True

    def get_capacity_info(self) -> dict[str, Any]:
        return {}


class NullMemoryPressureMonitor:
    """No-op memory pressure monitor for testing."""

    def check(self, _memory_usage_ratio: float) -> MemoryPressureLevel:
        return MemoryPressureLevel.NORMAL

    def update(self, level: MemoryPressureLevel) -> None:
        pass

    @property
    def current_level(self) -> MemoryPressureLevel:
        return MemoryPressureLevel.NORMAL

    def get_multiplier(self) -> float:
        return 1.0
