"""
Universal Coordinator Base
==========================

Consolidated base class integrating features from:
- DeepSeek R1 ModuleCoordinator (operation tracking, load factor, lifecycle)
- Hermes3 BaseCoordinator (simplified initialization, capabilities)
- M1 Master Optimizer memory awareness

Key Features Integrated:
1. Operation lifecycle management (track/untrack/generate_id)
2. Load factor calculation (0.0-1.0 with configurable max concurrent)
3. Graceful degradation (partial initialization support)
4. Memory-aware operation scheduling (M1 8GB optimization)
5. Async cleanup with resource management
6. Capabilities discovery and reporting
"""

from __future__ import annotations

import time
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Generic, List, Optional, TypeVar, Protocol
from collections import OrderedDict
import logging

from .enums import MemoryPressureLevel

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Universal operation types supported by coordinators."""
    RESEARCH = auto()
    EXECUTION = auto()
    SECURITY = auto()
    MONITORING = auto()
    SYNTHESIS = auto()
    OPTIMIZATION = auto()


@dataclass
class DecisionResponse:
    """Decision from orchestrator to be executed by coordinator."""
    decision_id: str
    chosen_option: str
    confidence: float
    reasoning: str
    estimated_duration: float = 0.0
    priority: int = 5  # 1-10, 10 being highest
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationResult:
    """Result of coordinator operation execution."""
    operation_id: str
    status: str  # "completed", "failed", "partial"
    result_summary: str
    execution_time: float
    success: bool
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class CoordinatorCapabilities:
    """Capabilities reported by a coordinator."""
    name: str
    supported_operations: List[OperationType]
    features: List[str]
    is_available: bool
    load_factor: float
    max_concurrent: int
    current_operations: int


class UniversalCoordinator(ABC):
    """
    Universal base class for all coordinators.

    Implements three coherent concerns directly:
    - OperationTrackingMixin: Operation lifecycle management
    - LoadFactorMixin: Capacity and load management
    - MemoryPressureMixin: M1 memory monitoring

    Previously used mixin classes as a compositional layer. After analysis,
    the "seam" they represented was theoretical — there was only ever one
    implementation of each behaviour, and all 12 coordinators inherit from
    UniversalCoordinator which already contains the concrete implementation.
    Introducing an injected support object would add indirection without
    corresponding testability benefit, given the mixins are stable and the
    coupling is confined to one class (UniversalCoordinator).
    """
    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
        memory_aware: bool = True
    ):
        self._name = name
        self._max_concurrent = max_concurrent
        self._memory_aware = memory_aware

        # Operation tracking (from OperationTrackingMixin)
        self._active_operations: Dict[str, Dict[str, Any]] = {}
        self._operation_counter = 0
        self._operation_history: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._max_history = 100

        # State
        self._initialized = False
        self._available = False
        self._initialization_error: Optional[str] = None

        # Memory awareness (from MemoryPressureMixin)
        self._current_memory_pressure = MemoryPressureLevel.NORMAL
        self._memory_thresholds = {
            MemoryPressureLevel.ELEVATED: 0.75,
            MemoryPressureLevel.HIGH: 0.85,
            MemoryPressureLevel.CRITICAL: 0.95,
        }

        # Metrics
        self._total_operations = 0
        self._successful_operations = 0
        self._failed_operations = 0
        self._total_execution_time = 0.0

    # =========================================================================
    # Abstract Methods - Must be implemented by subclasses
    # =========================================================================

    @abstractmethod
    def get_supported_operations(self) -> List[OperationType]:
        """Get list of operation types this coordinator supports."""
        pass

    @abstractmethod
    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
        """
        Handle a decision request.
        
        Args:
            operation_ref: Unique reference for this operation
            decision: Decision to execute
            
        Returns:
            OperationResult with execution outcome
        """
        pass

    @abstractmethod
    async def _do_initialize(self) -> bool:
        """
        Perform actual initialization. Override in subclasses.
        
        Returns:
            True if initialization successful, False otherwise
        """
        return True

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    async def initialize(self) -> bool:
        """
        Initialize coordinator with graceful degradation.
        
        Supports partial initialization - coordinator can be available
        even if some subsystems fail (from Hermes3 pattern).
        
        Returns:
            True if at least partially initialized
        """
        if self._initialized:
            return self._available

        try:
            self._available = await self._do_initialize()
            self._initialized = True
            
            if self._available:
                logger.info(f"Coordinator '{self._name}' initialized successfully")
            else:
                logger.warning(f"Coordinator '{self._name}' initialized with limited functionality")
                
        except Exception as e:
            self._initialization_error = str(e)
            self._available = False
            self._initialized = True
            logger.error(f"Coordinator '{self._name}' initialization failed: {e}")

        return self._available

    async def cleanup(self) -> None:
        """
        Cleanup coordinator resources.
        
        Safely handles cleanup even if initialization failed.
        """
        try:
            await self._do_cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup of '{self._name}': {e}")
        finally:
            self._active_operations.clear()
            self._initialized = False
            self._available = False

    async def _do_cleanup(self) -> None:
        """Override in subclasses for specific cleanup."""
        pass

    # =========================================================================
    # Operation Management
    # =========================================================================

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
            # Move to history
            op_data = self._active_operations.pop(operation_id)
            op_data['end_time'] = time.time()
            self._operation_history[operation_id] = op_data
            
            # Trim history if needed
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

    # =========================================================================
    # Load and Capacity Management
    # =========================================================================

    def get_load_factor(self) -> float:
        """
        Calculate current load factor (0.0 = idle, 1.0 = fully loaded).
        
        Considers:
        - Active operation count vs max concurrent
        - Current memory pressure (if memory_aware enabled)
        
        Returns:
            Load factor between 0.0 and 1.0
        """
        # Base load from active operations
        active_load = len(self._active_operations) / self._max_concurrent
        
        # Memory pressure adjustment (M1 optimization)
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
        # Always accept critical priority
        if priority >= 9:
            return self._available
            
        # Check load factor
        load = self.get_load_factor()
        
        # Different thresholds based on priority
        thresholds = {
            10: 1.0,   # Critical - always accept if available
            9: 0.95,   # Very high
            8: 0.90,   # High
            7: 0.85,
            6: 0.80,
            5: 0.75,   # Normal
            4: 0.70,
            3: 0.65,
            2: 0.60,
            1: 0.50,   # Low - only when idle
        }
        
        return load < thresholds.get(priority, 0.75)

    def get_capacity_info(self) -> Dict[str, Any]:
        """Get detailed capacity information."""
        return {
            'max_concurrent': self._max_concurrent,
            'active_operations': len(self._active_operations),
            'available_slots': self._max_concurrent - len(self._active_operations),
            'load_factor': self.get_load_factor(),
            'memory_pressure': self._current_memory_pressure.value,
            'can_accept_normal': self.can_accept_operation(priority=5),
            'can_accept_critical': self.can_accept_operation(priority=10),
        }

    # =========================================================================
    # Memory Management (M1 Master Optimizer Integration)
    # =========================================================================

    def update_memory_pressure(self, level: MemoryPressureLevel) -> None:
        """
        Update current memory pressure level.
        
        Args:
            level: New memory pressure level
        """
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
        if memory_usage_ratio >= self._memory_thresholds[MemoryPressureLevel.CRITICAL]:
            return MemoryPressureLevel.CRITICAL
        elif memory_usage_ratio >= self._memory_thresholds[MemoryPressureLevel.HIGH]:
            return MemoryPressureLevel.HIGH
        elif memory_usage_ratio >= self._memory_thresholds[MemoryPressureLevel.ELEVATED]:
            return MemoryPressureLevel.ELEVATED
        return MemoryPressureLevel.NORMAL

    # =========================================================================
    # Metrics and Reporting
    # =========================================================================

    def record_operation_result(self, result: OperationResult) -> None:
        """Record operation result for metrics."""
        self._total_operations += 1
        self._total_execution_time += result.execution_time
        
        if result.success:
            self._successful_operations += 1
        else:
            self._failed_operations += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Get coordinator performance metrics."""
        total = self._total_operations
        return {
            'total_operations': total,
            'successful': self._successful_operations,
            'failed': self._failed_operations,
            'success_rate': self._successful_operations / total if total > 0 else 0.0,
            'average_execution_time': (
                self._total_execution_time / total if total > 0 else 0.0
            ),
            'active_operations': len(self._active_operations),
            'history_size': len(self._operation_history),
        }

    def get_capabilities(self) -> CoordinatorCapabilities:
        """Get comprehensive coordinator capabilities."""
        return CoordinatorCapabilities(
            name=self._name,
            supported_operations=self.get_supported_operations(),
            features=self._get_feature_list(),
            is_available=self.is_available(),
            load_factor=self.get_load_factor(),
            max_concurrent=self._max_concurrent,
            current_operations=len(self._active_operations)
        )

    def _get_feature_list(self) -> List[str]:
        """Override in subclasses to report specific features."""
        return ["Basic coordination"]

    # =========================================================================
    # Status Methods
    # =========================================================================

    def get_name(self) -> str:
        """Get coordinator name."""
        return self._name

    def is_available(self) -> bool:
        """Check if coordinator is available for operations."""
        return self._available and self._initialized

    def is_initialized(self) -> bool:
        """Check if coordinator has been initialized."""
        return self._initialized

    def get_initialization_error(self) -> Optional[str]:
        """Get initialization error if any."""
        return self._initialization_error

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}(name='{self._name}', "
            f"available={self._available}, load={self.get_load_factor():.2f})>"
        )

    # =========================================================================
    # STABLE COORDINATOR INTERFACE (for Orchestrator Spine Pattern)
    # =========================================================================
    # This interface enables the orchestrator to become a thin "spine" that
    # delegates internal logic to coordinators via start/step/shutdown.
    # Context (ctx) is passed as dict - no raw text, only IDs/hashes/counters.
    # =========================================================================

    async def start(self, ctx: Dict[str, Any]) -> None:
        """
        Start the coordinator with context.

        Args:
            ctx: Context dict with orchestrator state (budgets, config, etc.)
        """
        await self.initialize()
        await self._do_start(ctx)

    async def _do_start(self, ctx: Dict[str, Any]) -> None:
        """
        Override in subclasses for specific start logic.
        Default: no-op.
        """
        pass

    async def step(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute one step of coordinator work.

        Args:
            ctx: Context dict with current state (frontier URLs, evidence IDs, etc.)

        Returns:
            Bounded dict with counts, IDs, and stop signals only:
            - urls_fetched: int
            - evidence_ids: List[str] (max K items)
            - clusters_updated: int
            - stop_reason: Optional[str]
            - Other bounded metrics
        """
        return await self._do_step(ctx)

    async def _do_step(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Override in subclasses for specific step logic.
        Default: empty response.
        """
        return {
            'urls_fetched': 0,
            'evidence_ids': [],
            'clusters_updated': 0,
            'stop_reason': None,
        }

    async def shutdown(self, ctx: Dict[str, Any]) -> None:
        """
        Shutdown the coordinator gracefully.

        Args:
            ctx: Context dict for cleanup state
        """
        await self._do_shutdown(ctx)
        await self.cleanup()

    async def _do_shutdown(self, ctx: Dict[str, Any]) -> None:
        """
        Override in subclasses for specific shutdown logic.
        Default: no-op.
        """
        pass
