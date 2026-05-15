"""
Coordinator Mixins
==================

Focused mixins for coordinator composition. Each mixin represents a single
coherent responsibility that can be independently tested and composed.

Mixins:
- OperationTrackingMixin: Operation lifecycle (track/untrack/generate_id)
- LoadFactorMixin: Capacity and load management
- MemoryPressureMixin: M1 memory monitoring (minimal - rarely used externally)
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from .enums import MemoryPressureLevel


class OperationTrackingMixin:
    """
    Operation lifecycle management mixin.

    Provides: generate_operation_id, track_operation, untrack_operation,
    get_active_operations, get_operation_status.

    Used by: execution, research, monitoring, security coordinators.
    """

    _name: str
    _operation_counter: int
    _active_operations: Dict[str, Dict[str, Any]]
    _operation_history: OrderedDict[str, Dict[str, Any]]
    _max_history: int

    def generate_operation_id(self) -> str:
        """Generate unique operation ID with coordinator prefix."""
        self._operation_counter += 1
        timestamp = int(time.time())
        return f"{self._name}_{timestamp}_{self._operation_counter:04d}"

    def track_operation(
        self,
        operation_id: str,
        operation_data: Dict[str, Any]
    ) -> None:
        """
        Track active operation.

        Args:
            operation_id: Unique operation identifier
            operation_data: Operation context and metadata
        """
        self._active_operations[operation_id] = {
            **operation_data,
            'start_time': time.time(),
            'coordinator': self._name,
        }

    def untrack_operation(self, operation_id: str) -> None:
        """
        Remove operation from active tracking and add to history.

        Args:
            operation_id: Operation to untrack
        """
        if operation_id in self._active_operations:
            op_data = self._active_operations.pop(operation_id)
            op_data['end_time'] = time.time()
            self._operation_history[operation_id] = op_data

            while len(self._operation_history) > self._max_history:
                self._operation_history.popitem(last=False)

    def get_active_operations(self) -> List[str]:
        """Get list of currently active operation IDs."""
        return list(self._active_operations.keys())

    def get_operation_status(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of specific operation.

        Args:
            operation_id: Operation to check

        Returns:
            Operation status dict or None if not found
        """
        if operation_id in self._active_operations:
            data = self._active_operations[operation_id]
            return {
                'status': 'active',
                'elapsed': time.time() - data['start_time'],
                **data
            }
        elif operation_id in self._operation_history:
            data = self._operation_history[operation_id]
            return {
                'status': 'completed',
                'duration': data['end_time'] - data['start_time'],
                **data
            }
        return None


class LoadFactorMixin:
    """
    Load factor and capacity management mixin.

    Provides: get_load_factor, can_accept_operation, get_capacity_info.

    Used internally by base class - not typically overridden by coordinators.
    """

    _max_concurrent: int
    _active_operations: Dict[str, Dict[str, Any]]
    _memory_aware: bool
    _current_memory_pressure: MemoryPressureLevel

    def get_load_factor(self) -> float:
        """
        Calculate current load factor (0.0 = idle, 1.0 = fully loaded).

        Considers:
        - Active operation count vs max concurrent
        - Current memory pressure (if memory_aware enabled)

        Returns:
            Load factor between 0.0 and 1.0
        """
        active_load = len(self._active_operations) / self._max_concurrent

        memory_multiplier = 1.0
        if self._memory_aware:
            if self._current_memory_pressure == MemoryPressureLevel.ELEVATED:
                memory_multiplier = 1.2
            elif self._current_memory_pressure == MemoryPressureLevel.HIGH:
                memory_multiplier = 1.5
            elif self._current_memory_pressure == MemoryPressureLevel.CRITICAL:
                memory_multiplier = 2.0

        return min(active_load * memory_multiplier, 1.0)

    def can_accept_operation(self, priority: int = 5) -> bool:
        """
        Check if coordinator can accept new operation.

        Args:
            priority: Operation priority (1-10, higher = more important)

        Returns:
            True if operation can be accepted
        """
        if priority >= 9:
            return getattr(self, '_available', False) and getattr(self, '_initialized', False)

        load = self.get_load_factor()

        thresholds = {
            10: 1.0,
            9: 0.95,
            8: 0.90,
            7: 0.85,
            6: 0.80,
            5: 0.75,
            4: 0.70,
            3: 0.65,
            2: 0.60,
            1: 0.50,
        }

        return load < thresholds.get(priority, 0.75)

    def get_capacity_info(self) -> Dict[str, Any]:
        """Get detailed capacity information."""
        return {
            'max_concurrent': self._max_concurrent,
            'active_operations': len(self._active_operations),
            'available_slots': self._max_concurrent - len(self._active_operations),
            'load_factor': self.get_load_factor(),
            'memory_pressure': self._current_memory_pressure.value if hasattr(self, '_current_memory_pressure') else 'normal',
            'can_accept_normal': self.can_accept_operation(priority=5),
            'can_accept_critical': self.can_accept_operation(priority=10),
        }


class MemoryPressureMixin:
    """
    Memory pressure monitoring mixin.

    Provides: update_memory_pressure, check_memory_pressure.

    Note: These methods are rarely called externally - the base class
    uses them internally for load factor calculation. Kept minimal
    in case coordinators need direct memory pressure management.
    """

    _name: str
    _current_memory_pressure: MemoryPressureLevel
    _memory_thresholds: Dict[MemoryPressureLevel, float]

    def update_memory_pressure(self, level: MemoryPressureLevel) -> None:
        """
        Update current memory pressure level.

        Args:
            level: New memory pressure level
        """
        import logging
        logger = logging.getLogger(__name__)

        if self._current_memory_pressure != level:
            logger.info(f"Coordinator '{self._name}' memory pressure: {level.value}")
            self._current_memory_pressure = level

    def check_memory_pressure(self, memory_usage_ratio: float) -> MemoryPressureLevel:
        """
        Check memory pressure based on usage ratio.

        Args:
            memory_usage_ratio: Current memory usage (0.0-1.0)

        Returns:
            Memory pressure level
        """
        thresholds = getattr(self, '_memory_thresholds', {
            MemoryPressureLevel.ELEVATED: 0.75,
            MemoryPressureLevel.HIGH: 0.85,
            MemoryPressureLevel.CRITICAL: 0.95,
        })

        if memory_usage_ratio >= thresholds[MemoryPressureLevel.CRITICAL]:
            return MemoryPressureLevel.CRITICAL
        elif memory_usage_ratio >= thresholds[MemoryPressureLevel.HIGH]:
            return MemoryPressureLevel.HIGH
        elif memory_usage_ratio >= thresholds[MemoryPressureLevel.ELEVATED]:
            return MemoryPressureLevel.ELEVATED
        return MemoryPressureLevel.NORMAL