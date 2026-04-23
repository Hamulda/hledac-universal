"""
Coordinator Registry
====================

Central registry for managing all Universal Coordinators.
Provides:
- Coordinator discovery and registration
- Load balancing across coordinators
- Operation routing based on capabilities
- Health monitoring of coordinators
- Statistics aggregation

Based on patterns from:
- DeepSeek R1: Operation routing and delegation
- Hermes3: Simplified coordinator management
- M1 Master: Memory-aware coordinator selection
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Type
from dataclasses import dataclass, field
import logging

from .base import (
    UniversalCoordinator,
    OperationType,
    DecisionResponse,
    OperationResult,
    CoordinatorCapabilities,
    MemoryPressureLevel
)

logger = logging.getLogger(__name__)


@dataclass
class CoordinatorInfo:
    """Information about a registered coordinator."""
    coordinator: UniversalCoordinator
    priority: int  # 1-10, higher = preferred
    weight: float  # For weighted load balancing
    registered_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class CoordinatorRegistry:
    """
    Central registry for managing Universal Coordinators.
    
    Features:
    - Register/unregister coordinators
    - Route operations to appropriate coordinator
    - Load balancing (least loaded, weighted, priority)
    - Health monitoring
    - Statistics aggregation
    """

    def __init__(self):
        self._coordinators: Dict[str, CoordinatorInfo] = {}
        self._by_operation: Dict[OperationType, List[str]] = {
            op_type: [] for op_type in OperationType
        }
        self._lock = asyncio.Lock()
        
        # Statistics
        self._routing_decisions = 0
        self._failed_routings = 0
        
        # Default coordinators cache
        self._defaults: Dict[OperationType, str] = {}

    # ========================================================================
    # Registration
    # ========================================================================

    async def register(
        self,
        coordinator: UniversalCoordinator,
        priority: int = 5,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Register a coordinator with the registry.
        
        Args:
            coordinator: Coordinator instance to register
            priority: Routing priority (1-10, higher = preferred)
            weight: Load balancing weight
            metadata: Additional metadata
            
        Returns:
            True if registration successful
        """
        async with self._lock:
            name = coordinator.get_name()
            
            # Initialize if needed
            if not coordinator.is_initialized():
                await coordinator.initialize()
            
            # Check availability
            if not coordinator.is_available():
                logger.warning(f"Coordinator '{name}' is not available, skipping registration")
                return False
            
            # Store coordinator info
            self._coordinators[name] = CoordinatorInfo(
                coordinator=coordinator,
                priority=priority,
                weight=weight,
                registered_at=time.monotonic(),
                metadata=metadata or {}
            )
            
            # Index by operation type
            for op_type in coordinator.get_supported_operations():
                if name not in self._by_operation[op_type]:
                    self._by_operation[op_type].append(name)
                    # Sort by priority (descending)
                    self._by_operation[op_type].sort(
                        key=lambda n: self._coordinators[n].priority,
                        reverse=True
                    )
            
            logger.info(f"Registered coordinator '{name}' with priority {priority}")
            return True

    async def unregister(self, name: str) -> bool:
        """
        Unregister a coordinator.
        
        Args:
            name: Coordinator name
            
        Returns:
            True if unregistration successful
        """
        async with self._lock:
            if name not in self._coordinators:
                return False
            
            # Get coordinator and cleanup
            info = self._coordinators[name]
            try:
                await info.coordinator.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up coordinator '{name}': {e}")
            
            # Remove from indexes
            del self._coordinators[name]
            
            for op_list in self._by_operation.values():
                if name in op_list:
                    op_list.remove(name)
            
            # Remove from defaults if set
            for op_type, default_name in list(self._defaults.items()):
                if default_name == name:
                    del self._defaults[op_type]
            
            logger.info(f"Unregistered coordinator '{name}'")
            return True

    # ========================================================================
    # Routing
    # ========================================================================

    async def route_operation(
        self,
        operation_type: OperationType,
        operation_ref: str,
        decision: DecisionResponse,
        strategy: str = "auto"
    ) -> OperationResult:
        """
        Route operation to appropriate coordinator.
        
        Args:
            operation_type: Type of operation
            operation_ref: Unique operation reference
            decision: Decision to execute
            strategy: Routing strategy ("auto", "priority", "load", "weighted")
            
        Returns:
            Operation result
        """
        async with self._lock:
            self._routing_decisions += 1
            
            # Get candidate coordinators
            candidates = self._by_operation.get(operation_type, [])
            if not candidates:
                self._failed_routings += 1
                return OperationResult(
                    operation_id="routing_failed",
                    status="failed",
                    result_summary=f"No coordinator available for {operation_type.name}",
                    execution_time=0.0,
                    success=False,
                    error_message=f"No coordinator registered for operation type {operation_type.name}"
                )
            
            # Select coordinator based on strategy
            if strategy == "priority":
                selected = self._select_by_priority(candidates, decision.priority)
            elif strategy == "load":
                selected = self._select_by_load(candidates)
            elif strategy == "weighted":
                selected = self._select_by_weight(candidates)
            else:  # auto
                selected = self._select_auto(candidates, decision.priority)
            
            if not selected:
                self._failed_routings += 1
                return OperationResult(
                    operation_id="routing_failed",
                    status="failed",
                    result_summary="No suitable coordinator available",
                    execution_time=0.0,
                    success=False,
                    error_message="All coordinators at capacity or unavailable"
                )
        
        # Execute outside lock
        coordinator = self._coordinators[selected].coordinator
        return await coordinator.handle_request(operation_ref, decision)

    def _select_by_priority(
        self,
        candidates: List[str],
        operation_priority: int
    ) -> Optional[str]:
        """Select coordinator with highest priority that can accept operation."""
        for name in candidates:
            info = self._coordinators[name]
            if info.coordinator.can_accept_operation(operation_priority):
                return name
        return None

    def _select_by_load(self, candidates: List[str]) -> Optional[str]:
        """Select coordinator with lowest load factor."""
        best_name = None
        best_load = 1.0
        
        for name in candidates:
            info = self._coordinators[name]
            load = info.coordinator.get_load_factor()
            if load < best_load and info.coordinator.can_accept_operation():
                best_load = load
                best_name = name
        
        return best_name

    def _select_by_weight(self, candidates: List[str]) -> Optional[str]:
        """Select coordinator using weighted random selection."""
        import random
        
        # Filter available coordinators
        available = [
            name for name in candidates
            if self._coordinators[name].coordinator.can_accept_operation()
        ]
        
        if not available:
            return None
        
        # Weighted selection
        weights = [self._coordinators[name].weight for name in available]
        total = sum(weights)
        
        if total == 0:
            return random.choice(available)
        
        r = random.uniform(0, total)
        cumulative = 0
        for name, weight in zip(available, weights):
            cumulative += weight
            if r <= cumulative:
                return name
        
        return available[-1]

    def _select_auto(
        self,
        candidates: List[str],
        operation_priority: int
    ) -> Optional[str]:
        """
        Auto-select best coordinator.
        
        Strategy:
        1. Try highest priority coordinator first
        2. If at capacity, try least loaded
        3. If none available, return None
        """
        # First try by priority
        selected = self._select_by_priority(candidates, operation_priority)
        if selected:
            return selected
        
        # Then try by load
        return self._select_by_load(candidates)

    # ========================================================================
    # Coordinator Access
    # ========================================================================

    def get_coordinator(self, name: str) -> Optional[UniversalCoordinator]:
        """Get coordinator by name."""
        info = self._coordinators.get(name)
        return info.coordinator if info else None

    def get_coordinators_for_operation(
        self,
        operation_type: OperationType
    ) -> List[UniversalCoordinator]:
        """Get all coordinators supporting an operation type."""
        names = self._by_operation.get(operation_type, [])
        return [self._coordinators[n].coordinator for n in names]

    def get_all_coordinators(self) -> List[UniversalCoordinator]:
        """Get all registered coordinators."""
        return [info.coordinator for info in self._coordinators.values()]

    def get_coordinator_names(self) -> List[str]:
        """Get names of all registered coordinators."""
        return list(self._coordinators.keys())

    # ========================================================================
    # Capabilities and Status
    # ========================================================================

    def get_all_capabilities(self) -> List[CoordinatorCapabilities]:
        """Get capabilities of all coordinators."""
        return [
            info.coordinator.get_capabilities()
            for info in self._coordinators.values()
        ]

    def get_capabilities_for_operation(
        self,
        operation_type: OperationType
    ) -> List[CoordinatorCapabilities]:
        """Get capabilities for coordinators supporting an operation type."""
        names = self._by_operation.get(operation_type, [])
        return [
            self._coordinators[n].coordinator.get_capabilities()
            for n in names
        ]

    def is_operation_supported(self, operation_type: OperationType) -> bool:
        """Check if any coordinator supports an operation type."""
        return len(self._by_operation.get(operation_type, [])) > 0

    def get_supported_operations(self) -> List[OperationType]:
        """Get all supported operation types."""
        return [
            op_type for op_type, names in self._by_operation.items()
            if names
        ]

    # ========================================================================
    # Health and Statistics
    # ========================================================================

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on all coordinators."""
        health = {
            'timestamp': time.monotonic(),
            'total_coordinators': len(self._coordinators),
            'coordinators': {}
        }
        
        for name, info in self._coordinators.items():
            coordinator = info.coordinator
            health['coordinators'][name] = {
                'available': coordinator.is_available(),
                'initialized': coordinator.is_initialized(),
                'load_factor': coordinator.get_load_factor(),
                'active_operations': len(coordinator.get_active_operations()),
                'supported_operations': [
                    op.name for op in coordinator.get_supported_operations()
                ]
            }
        
        # Overall status
        available = sum(
            1 for c in health['coordinators'].values() if c['available']
        )
        health['available_count'] = available
        health['status'] = 'healthy' if available == len(self._coordinators) else (
            'degraded' if available > 0 else 'unavailable'
        )
        
        return health

    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            'registered_coordinators': len(self._coordinators),
            'routing_decisions': self._routing_decisions,
            'failed_routings': self._failed_routings,
            'success_rate': (
                (self._routing_decisions - self._failed_routings) / 
                self._routing_decisions * 100
                if self._routing_decisions > 0 else 0
            ),
            'operations_supported': {
                op.name: len(names) 
                for op, names in self._by_operation.items() if names
            }
        }

    def get_load_distribution(self) -> Dict[str, float]:
        """Get load factor distribution across coordinators."""
        return {
            name: info.coordinator.get_load_factor()
            for name, info in self._coordinators.items()
        }

    # ========================================================================
    # Default Coordinators
    # ========================================================================

    def set_default(self, operation_type: OperationType, name: str) -> bool:
        """Set default coordinator for an operation type."""
        if name not in self._coordinators:
            return False
        
        coordinator = self._coordinators[name].coordinator
        if operation_type not in coordinator.get_supported_operations():
            return False
        
        self._defaults[operation_type] = name
        return True

    def get_default(self, operation_type: OperationType) -> Optional[str]:
        """Get default coordinator for an operation type."""
        return self._defaults.get(operation_type)

    # ========================================================================
    # Cleanup
    # ========================================================================

    async def cleanup_all(self) -> None:
        """Cleanup all registered coordinators."""
        for name, info in list(self._coordinators.items()):
            try:
                await info.coordinator.cleanup()
                logger.info(f"Cleaned up coordinator '{name}'")
            except Exception as e:
                logger.error(f"Error cleaning up coordinator '{name}': {e}")
        
        self._coordinators.clear()
        for op_list in self._by_operation.values():
            op_list.clear()
        self._defaults.clear()

    # ========================================================================
    # Context Manager
    # ========================================================================

    async def __aenter__(self) -> CoordinatorRegistry:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.cleanup_all()


# Global registry instance
_global_registry: Optional[CoordinatorRegistry] = None


def get_registry() -> CoordinatorRegistry:
    """Get global coordinator registry (singleton)."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CoordinatorRegistry()
    return _global_registry


def reset_registry() -> None:
    """Reset global registry (for testing)."""
    global _global_registry
    _global_registry = None


async def register_all_coordinators(
    memory_limit_mb: float = 5500,
    enable_advanced: bool = True
) -> CoordinatorRegistry:
    """
    Register all Universal Coordinators with the registry.
    
    Args:
        memory_limit_mb: Memory limit for memory coordinator
        enable_advanced: Whether to register advanced coordinators
        
    Returns:
        Configured registry with all coordinators
    """
    registry = get_registry()
    
    # Import all coordinators
    from .research_coordinator import UniversalResearchCoordinator
    from .execution_coordinator import UniversalExecutionCoordinator
    from .security_coordinator import UniversalSecurityCoordinator
    from .monitoring_coordinator import UniversalMonitoringCoordinator
    from .memory_coordinator import UniversalMemoryCoordinator
    from .advanced_research_coordinator import UniversalAdvancedResearchCoordinator
    from .swarm_coordinator import UniversalSwarmCoordinator
    from .multimodal_coordinator import UniversalMultimodalCoordinator
    from .meta_reasoning_coordinator import UniversalMetaReasoningCoordinator
    
    # Register core coordinators
    logger.info("Registering core coordinators...")
    
    await registry.register(
        UniversalMemoryCoordinator(memory_limit_mb=memory_limit_mb),
        priority=10,
        weight=2.0,
        metadata={'category': 'core', 'type': 'memory'}
    )
    
    await registry.register(
        UniversalResearchCoordinator(),
        priority=9,
        weight=1.5,
        metadata={'category': 'core', 'type': 'research'}
    )
    
    await registry.register(
        UniversalExecutionCoordinator(),
        priority=8,
        weight=1.0,
        metadata={'category': 'core', 'type': 'execution'}
    )
    
    await registry.register(
        UniversalSecurityCoordinator(),
        priority=8,
        weight=1.0,
        metadata={'category': 'core', 'type': 'security'}
    )
    
    await registry.register(
        UniversalMonitoringCoordinator(),
        priority=7,
        weight=0.8,
        metadata={'category': 'core', 'type': 'monitoring'}
    )
    
    # Register advanced coordinators
    if enable_advanced:
        logger.info("Registering advanced coordinators...")
        
        await registry.register(
            UniversalAdvancedResearchCoordinator(),
            priority=6,
            weight=1.0,
            metadata={'category': 'advanced', 'type': 'deep_excavation'}
        )
        
        await registry.register(
            UniversalSwarmCoordinator(),
            priority=5,
            weight=1.0,
            metadata={'category': 'advanced', 'type': 'swarm'}
        )

        await registry.register(
            UniversalMultimodalCoordinator(),
            priority=6,
            weight=1.0,
            metadata={'category': 'advanced', 'type': 'multimodal'}
        )

        await registry.register(
            UniversalMetaReasoningCoordinator(),
            priority=6,
            weight=1.0,
            metadata={'category': 'advanced', 'type': 'meta_reasoning'}
        )
    
    # Set defaults
    registry.set_default(OperationType.RESEARCH, 'universal_research_coordinator')
    registry.set_default(OperationType.EXECUTION, 'universal_execution_coordinator')
    registry.set_default(OperationType.SECURITY, 'universal_security_coordinator')
    registry.set_default(OperationType.REASONING, 'universal_meta_reasoning_coordinator')
    # OPTIMIZATION default removed - quantum_coordinator was removed
    # registry.set_default(OperationType.OPTIMIZATION, 'universal_quantum_coordinator')

    logger.info(f"Registered {len(registry.get_all_coordinators())} coordinators")
    
    return registry
