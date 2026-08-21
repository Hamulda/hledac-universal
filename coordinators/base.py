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

REFACTORED (F350M-R): Uses CoordinatorComponents for SRP compliance.
The three responsibilities are now isolated in composable components:
- OperationTracker: operation lifecycle
- LoadFactorCalculator: capacity management
- MemoryPressureMonitor: M1 memory monitoring
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from ._dto import CoordinatorCapabilities, DecisionResponse, ExecutionResult, OperationResult, OperationType
from .components import (
    LoadFactorCalculator,
    MemoryPressureLevel,
    MemoryPressureMonitor,
    NullLoadFactorCalculator,
    NullMemoryPressureMonitor,
    NullOperationTracker,
    OperationTracker,
)

logger = logging.getLogger(__name__)


class CoordinatorComponents:
    """
    Composition container for coordinator responsibilities.

    Provides a clean way to inject testable components while maintaining
    backward compatibility with existing code.

    Usage:
        from hledac.universal.coordinators.components import (
            OperationTracker,
            LoadFactorCalculator,
            MemoryPressureMonitor,
    )

        components = CoordinatorComponents(
            tracker=OperationTracker(name='test', max_concurrent=10),
            load=LoadFactorCalculator(tracker),
            memory=MemoryPressureMonitor(),
    )
    """

    __slots__ = ("tracker", "load", "memory")

    def __init__(
        self,
        tracker: OperationTracker | None = None,
        load: LoadFactorCalculator | None = None,
        memory: MemoryPressureMonitor | None = None,
    ) -> None:
        self.tracker = tracker or NullOperationTracker()
        self.load = load or NullLoadFactorCalculator()
        self.memory = memory or NullMemoryPressureMonitor()


class UniversalCoordinator(ABC):
    """
    Universal base class for all coordinators.

    Uses composition via CoordinatorComponents for SRP compliance:
    - OperationTracker: operation lifecycle tracking
    - LoadFactorCalculator: capacity and load management
    - MemoryPressureMonitor: M1 memory monitoring

    Subclasses should pass components explicitly or accept defaults.
    """

    __slots__ = (
        "_name",
        "_memory_aware",
        "_initialized",
        "_available",
        "_initialization_error",
        "_total_operations",
        "_successful_operations",
        "_failed_operations",
        "_total_execution_time",
        "_components",
    )

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
        memory_aware: bool = True,
    ) -> None:
        self._name = name
        self._memory_aware = memory_aware
        self._initialized = False
        self._available = False
        self._initialization_error: str | None = None
        self._total_operations = 0
        self._successful_operations = 0
        self._failed_operations = 0
        self._total_execution_time = 0.0

        # SRP: delegate to composable components
        tracker = OperationTracker(name=name, max_concurrent=max_concurrent)
        load = LoadFactorCalculator(tracker)
        memory = MemoryPressureMonitor()
        self._components = CoordinatorComponents(
            tracker=tracker,
            load=load,
            memory=memory,
        )

    @abstractmethod
    def get_supported_operations(self) -> list[OperationType]:
        """Get list of operation types this coordinator supports."""
        ...

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse,
    ) -> OperationResult:
        """
        Handle a decision request using template method pattern.

        Lifecycle:
        1. Generate operation ID
        2. Track operation
        3. Execute via _do_execute_decision()
        4. Record result
        5. Untrack operation

        Subclasses implement _do_execute_decision() instead of overriding this.
        """
        import time as time_mod

        start_time = time_mod.time()
        operation_id = self.generate_operation_id()
        operation_type = self._get_operation_type_for_tracking()

        try:
            self.track_operation(
                operation_id,
                {
                    "operation_ref": operation_ref,
                    "decision": decision,
                    "type": operation_type,
                },
            )
            exec_result = await self._do_execute_decision(decision)
            elapsed = time_mod.time() - start_time
            operation_result = OperationResult(
                operation_id=operation_id,
                status=exec_result.status,
                result_summary=exec_result.result_summary,
                execution_time=elapsed,
                success=exec_result.success,
                error_message=exec_result.error_message,
                metadata=exec_result.metadata,
            )
        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status="failed",
                result_summary=f"{self.__class__.__name__} operation failed: {str(e)}",
                execution_time=time_mod.time() - start_time,
                success=False,
                error_message=str(e),
            )
        finally:
            self.untrack_operation(operation_id)
        self.record_operation_result(operation_result)
        return operation_result

    def _get_operation_type_for_tracking(self) -> str:
        """Override to provide operation type for tracking."""
        return "coordinator"

    @abstractmethod
    async def _do_execute_decision(self, decision: DecisionResponse) -> ExecutionResult:
        """
        Execute the decision. Called by handle_request template.

        Args:
            decision: Decision to execute

        Returns:
            ExecutionResult with execution outcome
        """
        ...

    @abstractmethod
    async def _do_initialize(self) -> bool:
        """
        Perform actual initialization. Override in subclasses.

        Returns:
            True if initialization successful, False otherwise
        """
        ...

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
            # Clear active operations via tracker (safe for NullOperationTracker)
            active = getattr(self._components.tracker, "_active", None)
            if active is not None:
                active.clear()
            self._initialized = False
            self._available = False

    async def _do_cleanup(self) -> None:
        """Override in subclasses for specific cleanup."""

    def generate_operation_id(self) -> str:
        """Generate unique operation ID with coordinator prefix."""
        return self._components.tracker.generate_operation_id()

    def track_operation(self, operation_id: str, operation_data: dict[str, Any]) -> None:
        """
        Track active operation.

        Args:
            operation_id: Unique operation identifier
            operation_data: Operation context and metadata
        """
        self._components.tracker.track(operation_id, {**operation_data, "coordinator": self._name})

    def untrack_operation(self, operation_id: str) -> None:
        """
        Remove operation from active tracking and add to history.

        Args:
            operation_id: Operation to untrack
        """
        self._components.tracker.untrack(operation_id)

    def get_active_operations(self) -> list[str]:
        """Get list of currently active operation IDs."""
        return self._components.tracker.active_operations

    def get_operation_status(self, operation_id: str) -> dict[str, Any] | None:
        """
        Get status of specific operation.

        Args:
            operation_id: Operation to check

        Returns:
            Operation status dict or None if not found
        """
        return self._components.tracker.get_status(operation_id)

    def get_load_factor(self) -> float:
        """
        Calculate current load factor (0.0 = idle, 1.0 = fully loaded).

        Returns:
            Load factor between 0.0 and 1.0
        """
        return self._components.load.get_load_factor()

    def can_accept_operation(self, priority: int = 5) -> bool:
        """
        Check if coordinator can accept new operation.

        Args:
            priority: Operation priority (1-10, higher = more important)

        Returns:
            True if operation can be accepted
        """
        if priority >= 9:
            return self._available
        return self._components.load.can_accept(priority)

    def get_capacity_info(self) -> dict[str, Any]:
        """Get detailed capacity information."""
        info = self._components.load.get_capacity_info()
        info["memory_pressure"] = self._components.memory.current_level.value
        info["can_accept_normal"] = self.can_accept_operation(priority=5)
        info["can_accept_critical"] = self.can_accept_operation(priority=10)
        return info

    def update_memory_pressure(self, level: MemoryPressureLevel) -> None:
        """
        Update current memory pressure level.

        Args:
            level: New memory pressure level
        """
        self._components.memory.update(level)

    def check_memory_pressure(self, memory_usage_ratio: float) -> MemoryPressureLevel:
        """
        Check memory pressure based on usage ratio.

        Args:
            memory_usage_ratio: Current memory usage (0.0-1.0)

        Returns:
            Memory pressure level
        """
        return self._components.memory.check(memory_usage_ratio)

    def record_operation_result(self, result: OperationResult) -> None:
        """Record operation result for metrics."""
        self._total_operations += 1
        self._total_execution_time += result.execution_time
        if result.success:
            self._successful_operations += 1
        else:
            self._failed_operations += 1

    def get_metrics(self) -> dict[str, Any]:
        """Get coordinator performance metrics."""
        total = self._total_operations
        tracker = self._components.tracker
        return {
            "total_operations": total,
            "successful": self._successful_operations,
            "failed": self._failed_operations,
            "success_rate": self._successful_operations / total if total > 0 else 0.0,
            "average_execution_time": self._total_execution_time / total if total > 0 else 0.0,
            "active_operations": tracker.active_count,
            "history_size": len(getattr(tracker, "_history", [])),
        }

    def get_capabilities(self) -> CoordinatorCapabilities:
        """Get comprehensive coordinator capabilities."""
        tracker = self._components.tracker
        return CoordinatorCapabilities(
            name=self._name,
            supported_operations=self.get_supported_operations(),
            features=self._get_feature_list(),
            is_available=self.is_available(),
            load_factor=self.get_load_factor(),
            max_concurrent=tracker.max_concurrent,
            current_operations=tracker.active_count,
        )

    def _get_feature_list(self) -> list[str]:
        """Override in subclasses to report specific features."""
        return ["Basic coordination"]

    def get_name(self) -> str:
        """Get coordinator name."""
        return self._name

    def is_available(self) -> bool:
        """Check if coordinator is available for operations."""
        return self._available and self._initialized

    def is_initialized(self) -> bool:
        """Check if coordinator has been initialized."""
        return self._initialized

    def get_initialization_error(self) -> str | None:
        """Get initialization error if any."""
        return self._initialization_error

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self._name}', available={self._available}, load={self.get_load_factor():.2f})>"

    async def start(self, ctx: dict[str, Any]) -> None:
        """
        Start the coordinator with context.

        Args:
            ctx: Context dict with orchestrator state (budgets, config, etc.)
        """
        await self.initialize()
        await self._do_start(ctx)

    async def _do_start(self, ctx: dict[str, Any]) -> None:
        """
        Override in subclasses for specific start logic.
        Default: no-op.
        """

    async def step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one step of coordinator work.

        Args:
            ctx: Context dict with current state (frontier URLs, evidence IDs, etc.)

        Returns:
            Bounded dict with counts, IDs, and stop signals only:
            - urls_fetched: int
            - evidence_ids: list[str] (max K items)
            - clusters_updated: int
            - stop_reason: str | None
            - Other bounded metrics
        """
        return await self._do_step(ctx)

    async def _do_step(self, _ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Override in subclasses for specific step logic.
        Default: empty response.
        """
        return {"urls_fetched": 0, "evidence_ids": [], "clusters_updated": 0, "stop_reason": None}

    async def shutdown(self, ctx: dict[str, Any]) -> None:
        """
        Shutdown the coordinator gracefully.

        Args:
            ctx: Context dict for cleanup state
        """
        await self._do_shutdown(ctx)
        await self.cleanup()

    async def _do_shutdown(self, ctx: dict[str, Any]) -> None:
        """
        Override in subclasses for specific shutdown logic.
        Default: no-op.
        """
