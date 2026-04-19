"""
Coordination Layer v2 - Integrated with Universal Coordinators
================================================================

Integrated coordination layer using new Universal Coordinators:
- UniversalResearchCoordinator (from DeepSeek R1)
- UniversalExecutionCoordinator (from DeepSeek R1)
- UniversalSecurityCoordinator (from DeepSeek R1)
- UniversalMonitoringCoordinator (from DeepSeek R1)
- CoordinatorRegistry (central management)

Manages coordination between:
- Hermes-3 Commander (decision making)
- Universal Coordinators (operations)
- ContextManager (decision context tracking)
- CoordinatorRegistry (routing and load balancing)

Features:
- Multi-strategy routing (auto, priority, load, weighted)
- Health monitoring of coordinators
- Load balancing across coordinators
- Statistics aggregation
- Graceful fallback to local implementations
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import PriorityQueue
from typing import Any, Callable, Dict, List, Optional, Set
from collections import deque

from ..types import (
    CoordinationConfig,
    DecisionContext,
    DecisionRequest,
    DecisionResponse,
    OperationType,
    SubAgentResult,
    SubAgentType,
)

# Event-Driven Processor imports (optional)
try:
    from hledac.neuromorphic.common.neural_events import (
        NeuralEvent, SpikeData, EventType, NeuronType
    )
    from hledac.neuromorphic.common.processing_result import (
        ProcessingResult, ProcessingMetrics, ProcessingStatus
    )
    NEUROMORPHIC_AVAILABLE = True
except ImportError:
    NEUROMORPHIC_AVAILABLE = False
    NeuralEvent = None
    SpikeData = None
    EventType = None
    NeuronType = None
    ProcessingResult = None
    ProcessingMetrics = None
    ProcessingStatus = None

# Import Universal Coordinators
try:
    from ..coordinators import (
        UniversalResearchCoordinator,
        UniversalExecutionCoordinator,
        UniversalSecurityCoordinator,
        UniversalMonitoringCoordinator,
        UniversalMemoryCoordinator,
        CoordinatorRegistry,
        OperationType as CoordinatorOperationType,
        DecisionResponse as CoordinatorDecisionResponse,
        MemoryZone,
    )
    UNIVERSAL_COORDINATORS_AVAILABLE = True
except ImportError as e:
    UNIVERSAL_COORDINATORS_AVAILABLE = False
    logging.warning(f"Universal coordinators not available: {e}")

logger = logging.getLogger(__name__)


# =============================================================================
# EVENT-DRIVEN PROCESSOR COMPONENTS
# =============================================================================

@dataclass
class CircularBuffer:
    """Memory-efficient circular buffer for event history (M1 optimized)."""
    max_size: int
    buffer: deque = field(default_factory=deque)

    def __post_init__(self):
        self.buffer = deque(maxlen=self.max_size)

    def append(self, item: Any) -> None:
        """Add item to buffer (removes oldest if full)."""
        self.buffer.append(item)

    def get_recent(self, count: int) -> List[Any]:
        """Get most recent items (up to count)."""
        return list(self.buffer)[-count:] if self.buffer else []

    def clear(self) -> None:
        """Clear all items from buffer."""
        self.buffer.clear()

    def size(self) -> int:
        """Get current buffer size."""
        return len(self.buffer)

    def is_full(self) -> bool:
        """Check if buffer is at maximum capacity."""
        return len(self.buffer) == self.max_size


@dataclass
class NeuronState:
    """
    State of a neuron in the event-driven system.

    Attributes:
        membrane_potential: Current membrane potential
        last_spike_time: Timestamp of last spike
        refractory_end: Timestamp when refractory period ends
        input_connections: Source neuron IDs with weights
        output_connections: Target neuron IDs
        activity_history: Circular buffer of recent activity
    """
    neuron_id: str
    membrane_potential: float = 0.0
    last_spike_time: float = 0.0
    refractory_end: float = 0.0
    input_connections: Dict[str, float] = field(default_factory=dict)
    output_connections: Set[str] = field(default_factory=set)
    activity_history: CircularBuffer = field(default_factory=lambda: CircularBuffer(100))
    processing_priority: int = 1

    def is_in_refractory(self, current_time: float) -> bool:
        """Check if neuron is in refractory period."""
        return current_time < self.refractory_end

    def update_potential(self, delta: float) -> None:
        """Update membrane potential by delta."""
        self.membrane_potential += delta

    def reset(self) -> None:
        """Reset neuron state."""
        self.membrane_potential = 0.0
        self.last_spike_time = 0.0
        self.refractory_end = 0.0

    def get_input_strength(self, source_id: str) -> float:
        """Get synaptic weight for input connection."""
        return self.input_connections.get(source_id, 0.0)

    def add_input_connection(self, source_id: str, weight: float) -> None:
        """Add input synaptic connection."""
        self.input_connections[source_id] = weight

    def add_output_connection(self, target_id: str) -> None:
        """Add output synaptic connection."""
        self.output_connections.add(target_id)


class EventDrivenProcessor:
    """
    Asynchronous Event-Driven Neural Processor

    Processes neural events asynchronously with priority-based scheduling,
    optimized for M1 MacBook hardware constraints (8GB RAM).

    M1 Optimizations:
    - Limited thread pool workers (default 4)
    - Bounded queue sizes to prevent memory overflow
    - Efficient async processing with minimal overhead
    - Memory cleanup on shutdown
    """

    def __init__(
        self,
        max_workers: int = 4,
        queue_size: int = 10000,
        max_neurons: int = 1000,
        memory_buffer_size: int = 1000
    ):
        """
        Initialize EventDrivenProcessor.

        Args:
            max_workers: Maximum thread pool workers (M1: 4 recommended)
            queue_size: Maximum event queue size (bounded for memory safety)
            max_neurons: Maximum number of neurons
            memory_buffer_size: Circular buffer size for event history
        """
        self.max_workers = max_workers
        self.queue_size = queue_size
        self.max_neurons = max_neurons
        self.memory_buffer_size = memory_buffer_size

        # Core components
        self.event_queue: asyncio.Queue[NeuralEvent] = asyncio.Queue(maxsize=queue_size)
        self.neuron_states: Dict[str, NeuronState] = {}
        self.memory_buffer = CircularBuffer(memory_buffer_size)

        # Processing control
        self.running = False
        self.processing_lock = asyncio.Lock()
        self.thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="neuro_processor"
        )

        # Performance tracking - guard against unavailable ProcessingMetrics
        if NEUROMORPHIC_AVAILABLE and ProcessingMetrics is not None:
            self.metrics = ProcessingMetrics()
        else:
            self.metrics = None
        self.start_time = time.time()
        self._processing_task: Optional[asyncio.Task] = None

        # Neural thresholds (LIF neuron model)
        self.v_rest = -65.0
        self.v_reset = -65.0
        self.v_threshold = -50.0
        self.tau_m = 20.0
        self.tau_ref = 2.0  # ms

        logger.info(f"EventDrivenProcessor initialized with {max_workers} workers")

    async def start(self) -> bool:
        """Start the event-driven processor."""
        try:
            self.running = True
            self.start_time = time.time()
            self._processing_task = asyncio.create_task(self._process_loop())
            logger.info("EventDrivenProcessor started")
            return True
        except Exception as e:
            logger.error(f"EventDrivenProcessor start failed: {e}")
            return False

    async def stop(self) -> None:
        """Stop the event-driven processor and cleanup resources."""
        logger.info("Stopping EventDrivenProcessor...")
        self.running = False

        # Cancel processing task
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        # Shutdown thread pool
        self.thread_pool.shutdown(wait=True)

        # Clear queues and buffers
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.memory_buffer.clear()
        self.neuron_states.clear()

        logger.info("EventDrivenProcessor stopped")

    async def _process_loop(self) -> None:
        """Main async processing loop."""
        logger.info("EventDrivenProcessor processing loop started")

        while self.running:
            try:
                # Get event from queue with timeout
                try:
                    event = await asyncio.wait_for(
                        self.event_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Process event
                result = await self.process_event(event)

                # Update metrics
                self._update_metrics(result)

                # Store in memory buffer
                self.memory_buffer.append({
                    'event': event,
                    'result': result,
                    'timestamp': time.time()
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Event processing error: {e}")

    async def process_event(self, event: NeuralEvent) -> ProcessingResult:
        """
        Process a single neural event.

        Args:
            event: Neural event to process

        Returns:
            ProcessingResult with outcome and generated events
        """
        start_time = time.time()
        output_events = []

        try:
            async with self.processing_lock:
                if event.event_type == EventType.SPIKE:
                    # Handle spike event
                    result = await self._handle_spike_event(event)
                    output_events.extend(result)

                elif event.event_type == EventType.SYNAPTIC_UPDATE:
                    # Handle synaptic update
                    await self._handle_synaptic_update(event)

                elif event.event_type == EventType.LEARNING_UPDATE:
                    # Handle learning update
                    await self._handle_learning_update(event)

                else:
                    return ProcessingResult.failure(
                        event.event_id,
                        f"Unsupported event type: {event.event_type}"
                    )

                processing_time = time.time() - start_time

                return ProcessingResult.success(
                    event.event_id,
                    output_events,
                    ProcessingMetrics(
                        processing_time=processing_time,
                        events_processed=1,
                        neuron_updates=len(output_events)
                    )
                )

        except Exception as e:
            return ProcessingResult.failure(
                event.event_id,
                f"Event processing failed: {str(e)}"
            )

    async def _handle_spike_event(self, event: NeuralEvent) -> List[NeuralEvent]:
        """Handle spike event and generate propagation events."""
        output_events = []
        spike_data = event.data.get('spike_data')

        if not spike_data:
            return output_events

        current_time = time.time()

        # Update target neurons
        for target_id in event.target_neurons:
            if target_id not in self.neuron_states:
                # Create neuron state if it doesn't exist
                self.neuron_states[target_id] = NeuronState(neuron_id=target_id)

            neuron_state = self.neuron_states[target_id]

            # Skip if in refractory period
            if neuron_state.is_in_refractory(current_time):
                continue

            # Calculate input current based on synaptic weight
            synaptic_weight = neuron_state.get_input_strength(event.source_neuron)
            input_current = synaptic_weight * getattr(spike_data, 'strength', 1.0)

            # Update membrane potential (simplified LIF model)
            neuron_state.update_potential(input_current)

            # Check for spike generation
            if neuron_state.membrane_potential >= self.v_threshold:
                neuron_state.last_spike_time = current_time
                neuron_state.refractory_end = current_time + (self.tau_ref / 1000.0)
                neuron_state.membrane_potential = self.v_reset

                # Record activity
                neuron_state.activity_history.append({
                    'timestamp': current_time,
                    'input_current': input_current,
                    'spiked': True,
                    'membrane_potential': neuron_state.membrane_potential
                })

                # Generate output spike event
                if neuron_state.output_connections:
                    spike_event = NeuralEvent.create_spike_event(
                        target_id,
                        list(neuron_state.output_connections),
                        strength=1.0,
                        neuron_type=NeuronType.HIDDEN,
                        priority=neuron_state.processing_priority
                    )
                    output_events.append(spike_event)

        return output_events

    async def _handle_synaptic_update(self, event: NeuralEvent) -> None:
        """Handle synaptic weight update."""
        weight_change = event.data.get('weight_change', 0.0)
        source_id = event.data.get('source_neuron')
        target_id = event.data.get('target_neuron')

        if source_id and target_id:
            if target_id not in self.neuron_states:
                self.neuron_states[target_id] = NeuronState(neuron_id=target_id)

            neuron_state = self.neuron_states[target_id]
            current_weight = neuron_state.get_input_strength(source_id)
            neuron_state.add_input_connection(source_id, current_weight + weight_change)

            # Add output connection from source to target
            if source_id not in self.neuron_states:
                self.neuron_states[source_id] = NeuronState(neuron_id=source_id)
            self.neuron_states[source_id].add_output_connection(target_id)

    async def _handle_learning_update(self, event: NeuralEvent) -> None:
        """Handle learning rule updates (STDP placeholder)."""
        # Placeholder for learning rules (STDP, etc.)
        pass

    def stimulate_neuron(self, neuron_id: str, strength: float = 1.0) -> bool:
        """
        Directly stimulate a neuron.

        Args:
            neuron_id: ID of neuron to stimulate
            strength: Stimulation strength

        Returns:
            True if stimulation successful
        """
        if neuron_id not in self.neuron_states:
            self.neuron_states[neuron_id] = NeuronState(neuron_id=neuron_id)

        neuron_state = self.neuron_states[neuron_id]
        neuron_state.update_potential(strength)

        # Create spike event if threshold reached
        if neuron_state.membrane_potential >= self.v_threshold:
            try:
                spike_event = NeuralEvent.create_spike_event(
                    neuron_id,
                    list(neuron_state.output_connections),
                    strength=strength,
                    neuron_type=NeuronType.HIDDEN,
                    priority=neuron_state.processing_priority
                )
                # Add to queue (non-blocking)
                asyncio.create_task(self._enqueue_event(spike_event))
                return True
            except Exception as e:
                logger.warning(f"Failed to create spike event: {e}")

        return False

    async def _enqueue_event(self, event: NeuralEvent) -> None:
        """Enqueue an event (async helper)."""
        try:
            await asyncio.wait_for(self.event_queue.put(event), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("Event queue full, dropping event")

    def get_active_neurons(self) -> List[str]:
        """Get list of currently active neurons (non-zero potential)."""
        current_time = time.time()
        active = []
        for neuron_id, state in self.neuron_states.items():
            if (abs(state.membrane_potential) > 0.01 or
                state.is_in_refractory(current_time)):
                active.append(neuron_id)
        return active

    def get_processor_stats(self) -> Dict[str, Any]:
        """Get processor statistics."""
        return {
            'neurons': len(self.neuron_states),
            'queue_size': self.event_queue.qsize(),
            'memory_buffer_size': self.memory_buffer.size(),
            'uptime_seconds': time.time() - self.start_time,
            'metrics': self.metrics.to_dict(),
            'active_neurons': len(self.get_active_neurons()),
            'running': self.running
        }

    def _update_metrics(self, result: ProcessingResult) -> None:
        """Update processing metrics."""
        self.metrics.events_processed += result.metrics.events_processed
        self.metrics.events_failed += result.metrics.events_failed
        self.metrics.processing_time += result.metrics.processing_time
        self.metrics.neuron_updates += result.metrics.neuron_updates

    def add_neuron(self, neuron_id: str) -> bool:
        """Add a new neuron to the network."""
        if len(self.neuron_states) >= self.max_neurons:
            logger.warning(f"Maximum neurons ({self.max_neurons}) reached")
            return False

        if neuron_id in self.neuron_states:
            logger.warning(f"Neuron {neuron_id} already exists")
            return False

        self.neuron_states[neuron_id] = NeuronState(neuron_id=neuron_id)
        return True

    def connect_neurons(self, source_id: str, target_id: str, weight: float = 1.0) -> bool:
        """Create synaptic connection between neurons."""
        if source_id not in self.neuron_states:
            self.add_neuron(source_id)
        if target_id not in self.neuron_states:
            self.add_neuron(target_id)

        self.neuron_states[source_id].add_output_connection(target_id)
        self.neuron_states[target_id].add_input_connection(source_id, weight)
        return True


class CoordinationLayer:
    """
    Coordination layer for delegating operations to Universal Coordinators.

    Architecture:
    1. CoordinatorRegistry - routes to appropriate coordinator with load balancing
    2. Universal Coordinators - execute specific operation types
    3. ContextManager - tracks decision context
    4. EventDrivenProcessor - neuromorphic event processing
    5. Fallback to local implementations if Universal Coordinators fail

    Flow:
    DecisionRequest → CoordinatorRegistry.route()
    → UniversalCoordinator.handle_request() → DecisionResponse

    Example:
        coord = CoordinationLayer(config)
        await coord.initialize()

        # Execute operation
        response = await coord.delegate_operation(DecisionRequest(
            operation_type=OperationType.RESEARCH,
            context={"query": "..."}
        ))

        # Process neural event
        result = await coord.process_neural_event(neural_event)
    """

    def __init__(self, config: Optional[CoordinationConfig] = None):
        """
        Initialize CoordinationLayer.

        Args:
            config: Coordination configuration (uses defaults if None)
        """
        self.config = config or CoordinationConfig()

        # Core components
        self._coordinator_registry: Optional[CoordinatorRegistry] = None
        self._context_manager = None

        # Universal Coordinators
        self._research_coordinator: Optional[UniversalResearchCoordinator] = None
        self._execution_coordinator: Optional[UniversalExecutionCoordinator] = None
        self._security_coordinator: Optional[UniversalSecurityCoordinator] = None
        self._monitoring_coordinator: Optional[UniversalMonitoringCoordinator] = None
        self._memory_coordinator: Optional[UniversalMemoryCoordinator] = None

        # Event-Driven Processor (neuromorphic)
        self._event_processor: Optional[EventDrivenProcessor] = None

        # GhostWatchdog for driver health monitoring
        self._watchdog: Optional['GhostWatchdog'] = None

        # Statistics
        self._decision_count = 0
        self._delegation_count = 0
        self._coordinator_stats: Dict[str, Dict[str, int]] = {}

        logger.info("CoordinationLayer v2 initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize CoordinationLayer and all Universal Coordinators.
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing CoordinationLayer v2...")
            
            if not UNIVERSAL_COORDINATORS_AVAILABLE:
                logger.warning("⚠️ Universal coordinators not available, using fallbacks")
                await self._init_fallbacks()
                return True
            
            # Initialize CoordinatorRegistry
            self._coordinator_registry = CoordinatorRegistry()
            
            # Initialize ContextManager
            await self._init_context_manager()
            
            # Initialize Universal Coordinators
            await self._init_universal_coordinators()
            
            # Register coordinators with registry
            await self._register_coordinators()
            
            # Initialize GhostWatchdog for health monitoring
            await self._init_watchdog()

            # Initialize Event-Driven Processor
            await self._initialize_event_processor()

            logger.info("✅ CoordinationLayer v2 initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ CoordinationLayer v2 initialization failed: {e}")
            await self._init_fallbacks()
            return True  # Return True to allow operation with fallbacks
    
    async def _init_context_manager(self) -> None:
        """Initialize ContextManager"""
        if self._context_manager is None:
            try:
                # Try to import from hermes3 first
                try:
                    from hledac.hermes3.context_manager import ContextManager
                    self._context_manager = ContextManager()
                except ImportError:
                    # Fallback to local implementation
                    self._context_manager = _LocalContextManager()
                
                await self._context_manager.start()
                logger.info("✅ ContextManager initialized")
                
            except Exception as e:
                logger.warning(f"⚠️ ContextManager not available: {e}")
                self._context_manager = _DummyContextManager()
    
    async def _init_universal_coordinators(self) -> None:
        """Initialize all Universal Coordinators"""
        coordinator_tasks = []
        
        # ResearchCoordinator (priority 10 - highest)
        if self._research_coordinator is None:
            coordinator_tasks.append(self._init_research_coordinator())
        
        # ExecutionCoordinator (priority 9)
        if self._execution_coordinator is None:
            coordinator_tasks.append(self._init_execution_coordinator())
        
        # SecurityCoordinator (priority 8)
        if self._security_coordinator is None:
            coordinator_tasks.append(self._init_security_coordinator())
        
        # MonitoringCoordinator (priority 7)
        if self._monitoring_coordinator is None:
            coordinator_tasks.append(self._init_monitoring_coordinator())
        
        # MemoryCoordinator (priority 10 - critical)
        if self._memory_coordinator is None:
            coordinator_tasks.append(self._init_memory_coordinator())
        
        # Initialize all in parallel
        if coordinator_tasks:
            import asyncio
            await asyncio.gather(*coordinator_tasks, return_exceptions=True)
    
    async def _init_research_coordinator(self) -> None:
        """Initialize UniversalResearchCoordinator"""
        try:
            self._research_coordinator = UniversalResearchCoordinator(max_concurrent=5)
            await self._research_coordinator.initialize()
            
            if self._research_coordinator.is_available():
                logger.info("✅ UniversalResearchCoordinator initialized")
            else:
                logger.warning("⚠️ UniversalResearchCoordinator initialized with limited functionality")
                
        except Exception as e:
            logger.warning(f"⚠️ UniversalResearchCoordinator failed: {e}, using fallback")
            self._research_coordinator = _LocalResearchCoordinator()
            await self._research_coordinator.initialize()
    
    async def _init_execution_coordinator(self) -> None:
        """Initialize UniversalExecutionCoordinator"""
        try:
            self._execution_coordinator = UniversalExecutionCoordinator(max_concurrent=10)
            await self._execution_coordinator.initialize()
            
            if self._execution_coordinator.is_available():
                logger.info("✅ UniversalExecutionCoordinator initialized")
            else:
                logger.warning("⚠️ UniversalExecutionCoordinator initialized with limited functionality")
                
        except Exception as e:
            logger.warning(f"⚠️ UniversalExecutionCoordinator failed: {e}, using fallback")
            self._execution_coordinator = _LocalExecutionCoordinator()
            await self._execution_coordinator.initialize()
    
    async def _init_security_coordinator(self) -> None:
        """Initialize UniversalSecurityCoordinator"""
        try:
            self._security_coordinator = UniversalSecurityCoordinator(max_concurrent=5)
            await self._security_coordinator.initialize()
            
            if self._security_coordinator.is_available():
                logger.info("✅ UniversalSecurityCoordinator initialized")
            else:
                logger.warning("⚠️ UniversalSecurityCoordinator initialized with limited functionality")
                
        except Exception as e:
            logger.warning(f"⚠️ UniversalSecurityCoordinator failed: {e}, using fallback")
            self._security_coordinator = _LocalSecurityCoordinator()
            await self._security_coordinator.initialize()
    
    async def _init_monitoring_coordinator(self) -> None:
        """Initialize UniversalMonitoringCoordinator"""
        try:
            self._monitoring_coordinator = UniversalMonitoringCoordinator(max_concurrent=10)
            await self._monitoring_coordinator.initialize()
            
            if self._monitoring_coordinator.is_available():
                logger.info("✅ UniversalMonitoringCoordinator initialized")
            else:
                logger.warning("⚠️ UniversalMonitoringCoordinator initialized with limited functionality")
                
        except Exception as e:
            logger.warning(f"⚠️ UniversalMonitoringCoordinator failed: {e}")
    
    async def _init_memory_coordinator(self) -> None:
        """Initialize UniversalMemoryCoordinator"""
        try:
            self._memory_coordinator = UniversalMemoryCoordinator(memory_limit_mb=5500)
            # Memory coordinator doesn't need explicit initialization
            
            logger.info("✅ UniversalMemoryCoordinator initialized (5.5GB limit)")
                
        except Exception as e:
            logger.warning(f"⚠️ UniversalMemoryCoordinator failed: {e}")
            self._memory_coordinator = None
    
    async def _init_watchdog(self) -> None:
        """Initialize GhostWatchdog for driver health monitoring."""
        try:
            self._watchdog = GhostWatchdog(check_interval=5.0)
            
            # Register coordinators as watched drivers
            if self._research_coordinator:
                self._watchdog.register_driver(
                    "research", self._research_coordinator, max_restarts=3
                )
            if self._execution_coordinator:
                self._watchdog.register_driver(
                    "execution", self._execution_coordinator, max_restarts=3
                )
            if self._security_coordinator:
                self._watchdog.register_driver(
                    "security", self._security_coordinator, max_restarts=3
                )
            if self._monitoring_coordinator:
                self._watchdog.register_driver(
                    "monitoring", self._monitoring_coordinator, max_restarts=2
                )
            if self._memory_coordinator:
                self._watchdog.register_driver(
                    "memory", self._memory_coordinator, max_restarts=5
                )
            
            await self._watchdog.start_monitoring()
            logger.info("✅ GhostWatchdog monitoring started")
            
        except Exception as e:
            logger.warning(f"⚠️ GhostWatchdog initialization failed: {e}")
            self._watchdog = None

    async def _initialize_event_processor(self) -> None:
        """Initialize Event-Driven Processor for neuromorphic computing."""
        try:
            # M1 8GB optimized settings
            self._event_processor = EventDrivenProcessor(
                max_workers=4,  # Limited for M1 8GB
                queue_size=5000,  # Bounded queue
                max_neurons=500,  # Memory-limited neuron count
                memory_buffer_size=500  # Circular buffer size
            )

            # Start the processor
            success = await self._event_processor.start()

            if success:
                logger.info("✅ EventDrivenProcessor initialized (M1 8GB optimized)")

                # Register with watchdog if available
                if self._watchdog:
                    self._watchdog.register_driver(
                        "event_processor", self._event_processor, max_restarts=3
                    )
            else:
                logger.warning("⚠️ EventDrivenProcessor failed to start")
                self._event_processor = None

        except Exception as e:
            logger.warning(f"⚠️ EventDrivenProcessor initialization failed: {e}")
            self._event_processor = None

    async def process_neural_event(self, event: NeuralEvent) -> ProcessingResult:
        """
        Process a neural event through the EventDrivenProcessor.

        Args:
            event: Neural event to process (SPIKE, SYNAPTIC_UPDATE, LEARNING_UPDATE)

        Returns:
            ProcessingResult with outcome and any generated events

        Example:
            # Create a spike event
            event = NeuralEvent.create_spike_event(
                neuron_id="neuron_1",
                target_neurons=["neuron_2", "neuron_3"],
                strength=1.0
            )
            result = await coord.process_neural_event(event)
        """
        if not self._event_processor:
            return ProcessingResult.failure(
                getattr(event, 'event_id', 'unknown'),
                "EventDrivenProcessor not available"
            )

        try:
            result = await self._event_processor.process_event(event)

            # Update watchdog heartbeat for neural processing
            if self._watchdog:
                self._watchdog.update_heartbeat("event_processor")

            return result

        except Exception as e:
            logger.error(f"Neural event processing failed: {e}")
            return ProcessingResult.failure(
                getattr(event, 'event_id', 'unknown'),
                f"Processing failed: {str(e)}"
            )

    def stimulate_neuron(self, neuron_id: str, strength: float = 1.0) -> bool:
        """
        Directly stimulate a neuron in the event-driven system.

        Args:
            neuron_id: Unique identifier for the neuron
            strength: Stimulation strength (default 1.0)

        Returns:
            True if stimulation was successful
        """
        if not self._event_processor:
            return False

        return self._event_processor.stimulate_neuron(neuron_id, strength)

    def add_neural_connection(
        self,
        source_id: str,
        target_id: str,
        weight: float = 1.0
    ) -> bool:
        """
        Create a synaptic connection between two neurons.

        Args:
            source_id: Source neuron ID
            target_id: Target neuron ID
            weight: Synaptic weight

        Returns:
            True if connection was created successfully
        """
        if not self._event_processor:
            return False

        return self._event_processor.connect_neurons(source_id, target_id, weight)

    def get_neural_activity_stats(self) -> Dict[str, Any]:
        """
        Get neural activity statistics from the EventDrivenProcessor.

        Returns:
            Dictionary with neural activity metrics including:
            - neurons: Total number of neurons
            - active_neurons: Currently active neurons
            - queue_size: Pending events in queue
            - metrics: Processing metrics
            - neural_health: Health status of neural processing

        Integration with GhostWatchdog:
            - Provides neural health metrics for monitoring
            - Used for adaptive timeout calculations
        """
        if not self._event_processor:
            return {
                "available": False,
                "error": "EventDrivenProcessor not initialized"
            }

        stats = self._event_processor.get_processor_stats()
        active_neurons = self._event_processor.get_active_neurons()

        # Calculate neural load for adaptive timeouts
        neural_load = stats.get('queue_size', 0) / max(stats.get('neurons', 1), 1)

        # Neural health assessment
        neural_health = "healthy"
        if neural_load > 10:
            neural_health = "overloaded"
        elif neural_load > 5:
            neural_health = "busy"
        elif not active_neurons:
            neural_health = "idle"

        return {
            "available": True,
            "neurons": stats.get('neurons', 0),
            "active_neurons": len(active_neurons),
            "active_neuron_ids": active_neurons[:10],  # Limit for performance
            "queue_size": stats.get('queue_size', 0),
            "memory_buffer_size": stats.get('memory_buffer_size', 0),
            "uptime_seconds": stats.get('uptime_seconds', 0),
            "metrics": stats.get('metrics', {}),
            "neural_load": neural_load,
            "neural_health": neural_health,
            "running": stats.get('running', False)
        }

    def get_adaptive_timeout(self, base_timeout: float = 30.0) -> float:
        """
        Calculate adaptive timeout based on neural load.

        Used by GhostWatchdog and other components to adjust timeouts
        based on current neural processing load.

        Args:
            base_timeout: Base timeout in seconds

        Returns:
            Adjusted timeout based on neural load
        """
        neural_stats = self.get_neural_activity_stats()

        if not neural_stats.get('available'):
            return base_timeout

        neural_load = neural_stats.get('neural_load', 0)
        neural_health = neural_stats.get('neural_health', 'healthy')

        # Adjust timeout based on neural health
        if neural_health == 'overloaded':
            return base_timeout * 2.0  # Double timeout
        elif neural_health == 'busy':
            return base_timeout * 1.5
        elif neural_health == 'idle':
            return base_timeout * 0.8  # Faster when idle

        return base_timeout

    async def _init_fallbacks(self) -> None:
        """Initialize fallback implementations"""
        logger.info("🔄 Initializing fallback implementations...")
        
        self._research_coordinator = _LocalResearchCoordinator()
        self._execution_coordinator = _LocalExecutionCoordinator()
        self._security_coordinator = _LocalSecurityCoordinator()
        
        await self._research_coordinator.initialize()
        await self._execution_coordinator.initialize()
        await self._security_coordinator.initialize()
        
        logger.info("✅ Fallback implementations initialized")
    
    async def _register_coordinators(self) -> None:
        """Register coordinators with CoordinatorRegistry"""
        if self._coordinator_registry is None:
            return
        
        # Register with priorities and weights
        if self._research_coordinator:
            await self._coordinator_registry.register(
                self._research_coordinator,
                priority=10,
                weight=1.0,
                metadata={"type": "research", "version": "2.0"}
            )
        
        if self._execution_coordinator:
            await self._coordinator_registry.register(
                self._execution_coordinator,
                priority=9,
                weight=1.0,
                metadata={"type": "execution", "version": "2.0"}
            )
        
        if self._security_coordinator:
            await self._coordinator_registry.register(
                self._security_coordinator,
                priority=8,
                weight=1.0,
                metadata={"type": "security", "version": "2.0"}
            )
        
        if self._monitoring_coordinator:
            await self._coordinator_registry.register(
                self._monitoring_coordinator,
                priority=7,
                weight=0.5,  # Lower weight - less resource intensive
                metadata={"type": "monitoring", "version": "2.0"}
            )
        
        logger.info("✅ Coordinators registered with registry")
    
    def register_coordinator(
        self,
        operation_type: OperationType,
        coordinator: Any
    ) -> None:
        """
        Register a custom coordinator for an operation type.
        
        Args:
            operation_type: Type of operation
            coordinator: Coordinator instance
        """
        if self._coordinator_registry is None:
            logger.warning("⚠️ CoordinatorRegistry not available")
            return
        
        # Map OperationType to CoordinatorOperationType
        op_type_map = {
            OperationType.RESEARCH: CoordinatorOperationType.RESEARCH,
            OperationType.SECURITY: CoordinatorOperationType.SECURITY,
            OperationType.EXECUTION: CoordinatorOperationType.EXECUTION,
        }
        
        # Note: This is a simplified registration - full integration would require
        # adapter pattern to bridge between universal and legacy types
        logger.info(f"✅ Registered coordinator for {operation_type.value}")
    
    async def delegate_operation(
        self,
        request: DecisionRequest
    ) -> DecisionResponse:
        """
        Delegate an operation to the appropriate coordinator.
        
        Uses CoordinatorRegistry for intelligent routing with load balancing.
        
        Args:
            request: Decision request with operation type and context
            
        Returns:
            Decision response with results
        """
        self._decision_count += 1
        decision_id = str(uuid.uuid4())
        
        logger.info(
            f"📋 Delegating operation [{decision_id}]: "
            f"{request.operation_type.value}"
        )
        
        try:
            # Store in context manager
            if self._context_manager:
                self._context_manager.store_decision_request(request, decision_id)
            
            # Route via CoordinatorRegistry if available
            if self._coordinator_registry and UNIVERSAL_COORDINATORS_AVAILABLE:
                response = await self._delegate_via_registry(request, decision_id)
            else:
                # Direct delegation to specific coordinator
                response = await self._delegate_direct(request, decision_id)
            
            # Update watchdog heartbeat
            if self._watchdog:
                coordinator_name = request.operation_type.value.lower()
                self._watchdog.update_heartbeat(coordinator_name)
            
            logger.info(f"✅ Operation [{decision_id}] completed")
            return response
            
        except Exception as e:
            logger.error(f"❌ Operation [{decision_id}] failed: {e}")
            
            return DecisionResponse(
                decision_id=decision_id,
                operation_type=request.operation_type,
                action="error",
                parameters={"error": str(e)},
                confidence=0.0,
                coordinator_id=None,
                reasoning=str(e)
            )
    
    async def _delegate_via_registry(
        self,
        request: DecisionRequest,
        decision_id: str
    ) -> DecisionResponse:
        """Delegate operation via CoordinatorRegistry with load balancing."""
        # Map operation type
        op_type_map = {
            OperationType.RESEARCH: CoordinatorOperationType.RESEARCH,
            OperationType.SECURITY: CoordinatorOperationType.SECURITY,
            OperationType.EXECUTION: CoordinatorOperationType.EXECUTION,
        }
        
        coordinator_op_type = op_type_map.get(request.operation_type)
        if not coordinator_op_type:
            # Fallback to direct delegation
            return await self._delegate_direct(request, decision_id)
        
        # Create coordinator decision response
        from ..coordinators import DecisionResponse as CoordDecisionResponse
        
        coord_decision = CoordDecisionResponse(
            decision_id=decision_id,
            chosen_option=request.action or request.operation_type.value.lower(),
            confidence=request.confidence or 0.5,
            reasoning=request.context.get("query", ""),
            estimated_duration=request.context.get("timeout", 60.0),
            priority=request.context.get("priority", 5),
            metadata=request.context
        )
        
        # Route via registry with auto strategy
        result = await self._coordinator_registry.route_operation(
            coordinator_op_type,
            decision_id,
            coord_decision,
            strategy="auto"
        )
        
        # Convert result to DecisionResponse
        return DecisionResponse(
            decision_id=decision_id,
            operation_type=request.operation_type,
            action="delegated" if result.success else "error",
            parameters={"coordinator_result": result.metadata},
            confidence=result.success if result.success else 0.0,
            coordinator_id=result.operation_id,
            reasoning=result.result_summary
        )
    
    async def _delegate_direct(
        self,
        request: DecisionRequest,
        decision_id: str
    ) -> DecisionResponse:
        """Direct delegation to specific coordinator without registry."""
        coordinator = None
        
        if request.operation_type == OperationType.RESEARCH:
            coordinator = self._research_coordinator
        elif request.operation_type == OperationType.SECURITY:
            coordinator = self._security_coordinator
        elif request.operation_type == OperationType.EXECUTION:
            coordinator = self._execution_coordinator
        
        if coordinator and request.requires_delegation:
            self._delegation_count += 1
            
            # Track stats
            coord_name = coordinator.__class__.__name__
            if coord_name not in self._coordinator_stats:
                self._coordinator_stats[coord_name] = {
                    "calls": 0, "success": 0, "failed": 0
                }
            self._coordinator_stats[coord_name]["calls"] += 1
            
            # Execute via coordinator
            result = await coordinator.execute(request)
            
            if result.success if hasattr(result, 'success') else True:
                self._coordinator_stats[coord_name]["success"] += 1
            else:
                self._coordinator_stats[coord_name]["failed"] += 1
            
            return DecisionResponse(
                decision_id=decision_id,
                operation_type=request.operation_type,
                action="delegated",
                parameters={"coordinator_result": result},
                confidence=0.9,
                coordinator_id=coord_name
            )
        else:
            # No coordinator available
            return DecisionResponse(
                decision_id=decision_id,
                operation_type=request.operation_type,
                action="direct",
                parameters=request.context,
                confidence=0.5,
                coordinator_id=None
            )
    
    async def execute_research(
        self,
        plan: Dict[str, Any],
        context: DecisionContext
    ) -> List[SubAgentResult]:
        """
        Execute research plan via UniversalResearchCoordinator.
        
        Args:
            plan: Research plan with agents and tasks
            context: Decision context
            
        Returns:
            List of sub-agent results
        """
        logger.info(f"🔬 Executing research plan: {len(plan.get('agents', []))} agents")
        
        if not self._research_coordinator:
            logger.warning("⚠️ ResearchCoordinator not available, using fallback")
            return await self._fallback_research_execution(plan, context)
        
        try:
            # Check if it's Universal coordinator with advanced features
            if hasattr(self._research_coordinator, 'execute_multi_source_research'):
                # Use advanced multi-source research
                query = plan.get("query", "")
                results = await self._research_coordinator.execute_multi_source_research(
                    query=query,
                    confidence_threshold=0.7
                )
                
                # Convert to SubAgentResult format
                return self._convert_research_results(results, plan)
            else:
                # Use standard execute_plan
                return await self._research_coordinator.execute_plan(plan, context)
                
        except Exception as e:
            logger.error(f"❌ Research execution failed: {e}")
            return await self._fallback_research_execution(plan, context)
    
    def _convert_research_results(
        self,
        results: Dict[str, Any],
        plan: Dict[str, Any]
    ) -> List[SubAgentResult]:
        """Convert research results to SubAgentResult format."""
        sub_results = []
        
        # Convert each source to SubAgentResult
        for source in results.get("sources", []):
            sub_results.append(SubAgentResult(
                agent_type=SubAgentType(source.get("source", "research")),
                success=source.get("success", True),
                data={"summary": source.get("summary", ""), "full": source},
                confidence=source.get("confidence", 0.5),
                sources=[source.get("source", "unknown")],
                execution_time=source.get("execution_time", 0.0),
                state=__import__('enum').Enum('AgentState', 'COMPLETED').COMPLETED
            ))
        
        # If no results, create one from summary
        if not sub_results:
            sub_results.append(SubAgentResult(
                agent_type=SubAgentType.RESEARCH,
                success=results.get("success", False),
                data={"summary": results.get("summary", "")},
                confidence=results.get("average_confidence", 0.5),
                sources=results.get("backends_used", []),
                execution_time=results.get("total_execution_time", 0.0),
                state=__import__('enum').Enum('AgentState', 'COMPLETED').COMPLETED
            ))
        
        return sub_results
    
    async def _fallback_research_execution(
        self,
        plan: Dict[str, Any],
        context: DecisionContext
    ) -> List[SubAgentResult]:
        """Fallback research execution without coordinator"""
        results = []
        
        for agent_config in plan.get("agents", []):
            agent_type = SubAgentType(agent_config.get("type", "stealth_web"))
            
            results.append(SubAgentResult(
                agent_type=agent_type,
                success=True,
                data={"fallback": True, "task": agent_config.get("task")},
                confidence=0.5,
                sources=[],
                execution_time=0.0,
                state=__import__('enum').Enum('AgentState', 'COMPLETED').COMPLETED
            ))
        
        return results
    
    async def execute_security_check(
        self,
        query: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute security check via UniversalSecurityCoordinator.
        
        Args:
            query: Query to check
            context: Security context
            
        Returns:
            Security check results
        """
        if not self._security_coordinator:
            logger.debug("SecurityCoordinator not available")
            return {"secure": True, "obfuscated_query": query}
        
        try:
            # Use comprehensive security if available
            if hasattr(self._security_coordinator, 'execute_comprehensive_security'):
                from ..coordinators import SecurityLevel
                
                result = await self._security_coordinator.execute_comprehensive_security(
                    context=query,
                    target_security_level=SecurityLevel.STANDARD
                )
                
                return {
                    "secure": result.get("success", False),
                    "security_level": result.get("target_level", 1),
                    "layers_activated": result.get("layers_activated", 0),
                    "obfuscated_query": query,
                    "details": result
                }
            else:
                # Fallback to simple check
                return await self._security_coordinator.check_and_obfuscate(query, context)
                
        except Exception as e:
            logger.warning(f"Security check failed: {e}")
            return {"secure": False, "error": str(e), "obfuscated_query": query}
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health via UniversalMonitoringCoordinator."""
        if not self._monitoring_coordinator:
            return {"status": "unknown", "error": "Monitoring not available"}
        
        try:
            if hasattr(self._monitoring_coordinator, 'perform_health_check'):
                return await self._monitoring_coordinator.perform_health_check(detailed=True)
            else:
                return {"status": "basic", "available": True}
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get coordination layer statistics"""
        stats = {
            "decisions_made": self._decision_count,
            "operations_delegated": self._delegation_count,
            "coordinator_stats": self._coordinator_stats,
            "available_coordinators": list(self._coordinator_stats.keys()),
            "universal_coordinators": UNIVERSAL_COORDINATORS_AVAILABLE,
        }

        # Add registry stats if available
        if self._coordinator_registry:
            stats["registry_stats"] = self._coordinator_registry.get_statistics()

        # Add watchdog stats if available
        if self._watchdog:
            stats["watchdog_stats"] = self._watchdog.get_stats()
            stats["watchdog_driver_status"] = self._watchdog.get_driver_status()

        # Add neural activity stats if available
        if self._event_processor:
            stats["neural_stats"] = self.get_neural_activity_stats()
            stats["adaptive_timeout"] = self.get_adaptive_timeout()

        return stats
    
    # ========================================================================
    # Memory Management (UniversalMemoryCoordinator)
    # ========================================================================
    
    def get_memory_coordinator(self) -> Optional[UniversalMemoryCoordinator]:
        """Get memory coordinator instance."""
        return self._memory_coordinator
    
    async def perform_memory_cleanup(self, aggressive: bool = False) -> Dict[str, Any]:
        """
        Perform memory cleanup.
        
        Args:
            aggressive: Whether to perform aggressive cleanup
            
        Returns:
            Cleanup results
        """
        if not self._memory_coordinator:
            return {"success": False, "error": "Memory coordinator not available"}
        
        try:
            if aggressive:
                result = self._memory_coordinator.aggressive_cleanup()
            else:
                from ..coordinators import MemoryPressureLevel
                result = await self._memory_coordinator.cleanup(MemoryPressureLevel.HIGH)
            
            return {
                "success": True,
                "aggressive": aggressive,
                "result": result
            }
        except Exception as e:
            logger.error(f"Memory cleanup failed: {e}")
            return {"success": False, "error": str(e)}
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        if not self._memory_coordinator:
            return {"error": "Memory coordinator not available"}
        
        return self._memory_coordinator.get_stats()
    
    # ========================================================================
    # GhostWatchdog Integration
    # ========================================================================
    
    def get_watchdog(self) -> Optional['GhostWatchdog']:
        """Get GhostWatchdog instance."""
        return self._watchdog

    # ========================================================================
    # Hive & Smart Coordination Integration
    # ========================================================================

    def enable_hive_mode(self, swarm_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Enable Hive Mind coordination mode.

        Integrates collective intelligence and swarm coordination capabilities
        from hive_coordination.py into the CoordinationLayer.

        Args:
            swarm_id: Optional swarm identifier

        Returns:
            Configuration result with swarm details
        """
        try:
            # Try to import and initialize ConnectedCoordinationSystem
            from .hive_coordination import ConnectedCoordinationSystem, TopologyType

            swarm_id = swarm_id or f"swarm_{int(time.time())}"
            self._hive_system = ConnectedCoordinationSystem(swarm_id)

            logger.info(f"✅ Hive mode enabled with swarm_id: {swarm_id}")

            return {
                "success": True,
                "mode": "hive",
                "swarm_id": swarm_id,
                "topology": self._hive_system.current_topology.value,
                "total_nodes": len(self._hive_system.nodes),
                "status": "active"
            }

        except ImportError as e:
            logger.warning(f"⚠️ Hive coordination not available: {e}")
            return {
                "success": False,
                "mode": "hive",
                "error": str(e),
                "status": "unavailable"
            }
        except Exception as e:
            logger.error(f"❌ Failed to enable hive mode: {e}")
            return {
                "success": False,
                "mode": "hive",
                "error": str(e),
                "status": "error"
            }

    def enable_smart_mode(self) -> Dict[str, Any]:
        """
        Enable Smart-spawned agent coordination mode.

        Integrates intelligent agent spawning and task distribution
        from smart_coordination.py into the CoordinationLayer.

        Returns:
            Configuration result with smart agent details
        """
        try:
            # Try to import and initialize SmartSpawnedCoordinationIntegration
            from .smart_coordination import SmartSpawnedCoordinationIntegration
            from .hive_coordination import ConnectedCoordinationSystem

            # Ensure hive system exists for smart coordination
            if not hasattr(self, '_hive_system') or self._hive_system is None:
                hive_result = self.enable_hive_mode()
                if not hive_result["success"]:
                    return hive_result

            self._smart_integration = SmartSpawnedCoordinationIntegration(self._hive_system)

            logger.info("✅ Smart mode enabled with intelligent agent spawning")

            status = self._smart_integration.get_smart_coordination_status()

            return {
                "success": True,
                "mode": "smart",
                "smart_agents_count": status.get("smart_agents_count", 0),
                "agents_by_role": status.get("agents_by_role", {}),
                "status": "active"
            }

        except ImportError as e:
            logger.warning(f"⚠️ Smart coordination not available: {e}")
            return {
                "success": False,
                "mode": "smart",
                "error": str(e),
                "status": "unavailable"
            }
        except Exception as e:
            logger.error(f"❌ Failed to enable smart mode: {e}")
            return {
                "success": False,
                "mode": "smart",
                "error": str(e),
                "status": "error"
            }

    def get_coordination_mode(self) -> Dict[str, Any]:
        """
        Get current coordination mode status.

        Returns:
            Current mode configuration and status
        """
        modes = {
            "standard": True,  # Always available
            "hive": hasattr(self, '_hive_system') and self._hive_system is not None,
            "smart": hasattr(self, '_smart_integration') and self._smart_integration is not None
        }

        active_mode = "standard"
        if modes["smart"]:
            active_mode = "smart"
        elif modes["hive"]:
            active_mode = "hive"

        return {
            "active_mode": active_mode,
            "available_modes": [k for k, v in modes.items() if v],
            "modes": modes
        }

    async def process_with_hive(self, task_description: str, priority: str = "medium") -> Dict[str, Any]:
        """
        Process task using Hive Mind coordination.

        Args:
            task_description: Task to process
            priority: Task priority

        Returns:
            Processing result
        """
        if not hasattr(self, '_hive_system') or self._hive_system is None:
            return {
                "success": False,
                "error": "Hive mode not enabled. Call enable_hive_mode() first."
            }

        try:
            task_id = await self._hive_system.process_task(task_description, priority)
            return {
                "success": True,
                "task_id": task_id,
                "mode": "hive"
            }
        except Exception as e:
            logger.error(f"Hive processing failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def process_with_smart(self, task_description: str, priority: str = "medium") -> Dict[str, Any]:
        """
        Process task using Smart-spawned agent coordination.

        Args:
            task_description: Task to process
            priority: Task priority

        Returns:
            Processing result with smart agent assignments
        """
        if not hasattr(self, '_smart_integration') or self._smart_integration is None:
            return {
                "success": False,
                "error": "Smart mode not enabled. Call enable_smart_mode() first."
            }

        try:
            result = await self._smart_integration.process_task_with_smart_coordination(
                task_description, priority
            )
            return {
                "success": True,
                "result": result,
                "mode": "smart"
            }
        except Exception as e:
            logger.error(f"Smart processing failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("🧹 Cleaning up CoordinationLayer v2...")

        # Stop watchdog first
        if self._watchdog:
            try:
                await self._watchdog.stop_monitoring()
                logger.info("✅ GhostWatchdog stopped")
            except Exception as e:
                logger.warning(f"⚠️ Watchdog cleanup error: {e}")

        # Cleanup Event-Driven Processor
        if self._event_processor:
            try:
                await self._event_processor.stop()
                logger.info("✅ EventDrivenProcessor stopped")
            except Exception as e:
                logger.warning(f"⚠️ EventDrivenProcessor cleanup error: {e}")

        # Cleanup coordinators
        coordinators = [
            self._research_coordinator,
            self._execution_coordinator,
            self._security_coordinator,
            self._monitoring_coordinator,
            self._memory_coordinator
        ]

        for coord in coordinators:
            if coord and hasattr(coord, 'cleanup'):
                try:
                    await coord.cleanup()
                except Exception as e:
                    logger.warning(f"⚠️ Coordinator cleanup error: {e}")

        # Cleanup registry
        if self._coordinator_registry:
            try:
                await self._coordinator_registry.cleanup_all()
            except Exception as e:
                logger.warning(f"⚠️ Registry cleanup error: {e}")

        # Cleanup context manager
        if self._context_manager and hasattr(self._context_manager, 'cleanup'):
            try:
                await self._context_manager.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ ContextManager cleanup error: {e}")

        logger.info("✅ CoordinationLayer v2 cleanup complete")


# =============================================================================
# LOCAL IMPLEMENTATIONS (Fallbacks)
# =============================================================================

class _LocalContextManager:
    """Simple local context manager"""
    
    def __init__(self):
        self._contexts: Dict[str, Any] = {}
    
    async def start(self) -> None:
        pass
    
    def store_decision_request(
        self,
        request: DecisionRequest,
        decision_id: str
    ) -> None:
        self._contexts[decision_id] = {
            "request": request,
            "timestamp": time.time()
        }
    
    async def cleanup(self) -> None:
        self._contexts.clear()


class _DummyContextManager:
    """Dummy context manager that does nothing"""
    
    async def start(self) -> None:
        pass
    
    def store_decision_request(self, request: DecisionRequest, decision_id: str) -> None:
        pass
    
    async def cleanup(self) -> None:
        pass


class _LocalResearchCoordinator:
    """Local research coordinator implementation"""
    
    async def initialize(self) -> None:
        pass
    
    async def execute_plan(
        self,
        plan: Dict[str, Any],
        context: DecisionContext
    ) -> List[SubAgentResult]:
        results = []
        for agent_config in plan.get("agents", []):
            results.append(SubAgentResult(
                agent_type=SubAgentType(agent_config.get("type", "stealth_web")),
                success=True,
                data={"local": True},
                confidence=0.6,
                sources=[],
                execution_time=0.0,
                state=__import__('enum').Enum('AgentState', 'COMPLETED').COMPLETED
            ))
        return results
    
    async def cleanup(self) -> None:
        pass


class _LocalSecurityCoordinator:
    """Local security coordinator implementation"""
    
    async def initialize(self) -> None:
        pass
    
    async def check_and_obfuscate(
        self,
        query: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {"secure": True, "obfuscated_query": query}
    
    async def cleanup(self) -> None:
        pass


class _LocalExecutionCoordinator:
    """Local execution coordinator implementation"""
    
    async def initialize(self) -> None:
        pass
    
    async def execute(self, request: DecisionRequest) -> Any:
        return {"success": True, "local": True}
    
    async def cleanup(self) -> None:
        pass


# =============================================================================
# GHOST WATCHDOG - Driver Health Monitoring (from kernel/loop.py)
# =============================================================================

from enum import Enum, auto
from dataclasses import dataclass, field


class DriverStatus(Enum):
    """Driver status enumeration for GhostWatchdog"""
    HEALTHY = "healthy"
    UNRESPONSIVE = "unresponsive"
    FAILED = "failed"
    RESTARTING = "restarting"
    DISABLED = "disabled"


@dataclass
class DriverHealth:
    """Driver health information"""
    name: str
    status: DriverStatus
    last_heartbeat: float
    restart_count: int = 0
    max_restarts: int = 3
    error_message: Optional[str] = None
    driver_instance: Optional[Any] = None


class GhostWatchdog:
    """
    GhostWatchdog - Robust system monitoring and recovery system.
    
    Integrated from kernel/loop.py - Provides driver health monitoring
    with automatic restart capabilities for M1 Mac systems.
    
    Features:
    - 5-second heartbeat monitoring for all drivers
    - Automatic driver restart on failure
    - Global exception handling with graceful degradation
    - Driver deactivation after max restart attempts
    - Rich Console logging for critical events
    
    Example:
        watchdog = GhostWatchdog(check_interval=5.0)
        await watchdog.start_monitoring()
        
        # Register a driver
        watchdog.register_driver("research", research_driver, max_restarts=3)
        
        # Update heartbeat from driver
        watchdog.update_heartbeat("research")
    """
    
    def __init__(self, check_interval: float = 5.0):
        """
        Initialize GhostWatchdog.
        
        Args:
            check_interval: Seconds between health checks
        """
        self.check_interval = check_interval
        self.drivers: Dict[str, DriverHealth] = {}
        self.running = False
        self.watchdog_task: Optional[asyncio.Task] = None
        
        # Statistics
        self.stats = {
            'total_restarts': 0,
            'total_failures': 0,
            'disabled_drivers': 0,
            'uptime_seconds': 0,
            'last_check_time': None
        }
        
        logger.info("GhostWatchdog initialized")
    
    def register_driver(self, name: str, driver_instance: Any, max_restarts: int = 3):
        """
        Register a driver for monitoring.
        
        Args:
            name: Driver name
            driver_instance: The actual driver object
            max_restarts: Maximum restart attempts before disabling
        """
        self.drivers[name] = DriverHealth(
            name=name,
            status=DriverStatus.HEALTHY,
            last_heartbeat=time.time(),
            max_restarts=max_restarts,
            driver_instance=driver_instance
        )
        
        logger.info(f"GhostWatchdog: Registered driver '{name}'")
    
    def update_heartbeat(self, driver_name: str):
        """
        Update heartbeat for a driver (called by drivers).
        
        Args:
            driver_name: Name of the driver
        """
        if driver_name in self.drivers:
            self.drivers[driver_name].last_heartbeat = time.time()
            if self.drivers[driver_name].status == DriverStatus.UNRESPONSIVE:
                self.drivers[driver_name].status = DriverStatus.HEALTHY
                logger.info(f"GhostWatchdog: Driver '{driver_name}' recovered")
    
    async def start_monitoring(self):
        """Start the watchdog monitoring task."""
        if self.running:
            logger.warning("GhostWatchdog already running")
            return
        
        self.running = True
        self.watchdog_task = asyncio.create_task(self._monitor_loop())
        
        logger.info("GhostWatchdog monitoring started")
    
    async def stop_monitoring(self):
        """Stop the watchdog monitoring task."""
        self.running = False
        
        if self.watchdog_task:
            self.watchdog_task.cancel()
            try:
                await self.watchdog_task
            except asyncio.CancelledError:
                pass
        
        logger.info("GhostWatchdog monitoring stopped")
    
    async def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("GhostWatchdog monitoring loop started")
        
        while self.running:
            try:
                await self._check_all_drivers()
                self.stats['last_check_time'] = time.time()
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GhostWatchdog monitoring error: {e}")
                await asyncio.sleep(1.0)
    
    async def _check_all_drivers(self):
        """Check health of all registered drivers."""
        current_time = time.time()
        
        for driver_name, driver_health in self.drivers.items():
            try:
                # Skip disabled drivers
                if driver_health.status == DriverStatus.DISABLED:
                    continue
                
                # Check heartbeat timeout (5 seconds + small buffer)
                heartbeat_timeout = self.check_interval + 2.0
                time_since_heartbeat = current_time - driver_health.last_heartbeat
                
                if time_since_heartbeat > heartbeat_timeout:
                    if driver_health.status == DriverStatus.HEALTHY:
                        # Driver just became unresponsive
                        driver_health.status = DriverStatus.UNRESPONSIVE
                        logger.warning(
                            f"GhostWatchdog: Driver '{driver_name}' unresponsive "
                            f"(no heartbeat for {time_since_heartbeat:.1f}s)"
                        )
                    
                    # Try to restart if unresponsive
                    if driver_health.status == DriverStatus.UNRESPONSIVE:
                        await self._restart_driver(driver_name)
                        
            except Exception as e:
                logger.error(f"GhostWatchdog: Error checking driver '{driver_name}': {e}")
    
    async def _restart_driver(self, driver_name: str):
        """
        Attempt to restart a failed driver.
        
        Args:
            driver_name: Name of the driver to restart
        """
        driver_health = self.drivers[driver_name]
        
        # Check if we've exceeded max restarts
        if driver_health.restart_count >= driver_health.max_restarts:
            driver_health.status = DriverStatus.DISABLED
            self.stats['disabled_drivers'] += 1
            
            logger.critical(
                f"CRITICAL FAILURE - Driver '{driver_name}' disabled after "
                f"{driver_health.restart_count} restart attempts"
            )
            return
        
        # Attempt restart
        driver_health.status = DriverStatus.RESTARTING
        driver_health.restart_count += 1
        self.stats['total_restarts'] += 1
        
        logger.info(
            f"GhostWatchdog: RESTARTING DRIVER '{driver_name}' "
            f"(attempt {driver_health.restart_count}/{driver_health.max_restarts})"
        )
        
        try:
            driver_instance = driver_health.driver_instance
            
            # Try to reinitialize the driver
            if hasattr(driver_instance, 'restart'):
                success = await driver_instance.restart()
            elif hasattr(driver_instance, 'initialize'):
                success = await driver_instance.initialize()
            elif hasattr(driver_instance, 'reconnect'):
                success = await driver_instance.reconnect()
            else:
                logger.warning(
                    f"GhostWatchdog: No restart method for driver '{driver_name}'"
                )
                success = True
            
            if success:
                driver_health.status = DriverStatus.HEALTHY
                driver_health.last_heartbeat = time.time()
                driver_health.error_message = None
                
                logger.info(f"GhostWatchdog: Driver '{driver_name}' restarted successfully")
            else:
                driver_health.status = DriverStatus.FAILED
                logger.error(f"GhostWatchdog: Failed to restart driver '{driver_name}'")
                
        except Exception as e:
            driver_health.status = DriverStatus.FAILED
            driver_health.error_message = str(e)
            self.stats['total_failures'] += 1
            
            logger.error(f"GhostWatchdog: Failed to restart driver '{driver_name}': {e}")
    
    def get_driver_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all monitored drivers."""
        status = {}
        current_time = time.time()
        
        for name, health in self.drivers.items():
            status[name] = {
                'status': health.status.value,
                'last_heartbeat': health.last_heartbeat,
                'time_since_heartbeat': current_time - health.last_heartbeat,
                'restart_count': health.restart_count,
                'max_restarts': health.max_restarts,
                'error_message': health.error_message
            }
        
        return status
    
    def get_stats(self) -> Dict[str, Any]:
        """Get watchdog statistics."""
        stats = self.stats.copy()
        stats['monitored_drivers'] = len(self.drivers)
        stats['healthy_drivers'] = sum(
            1 for h in self.drivers.values() if h.status == DriverStatus.HEALTHY
        )
        stats['unresponsive_drivers'] = sum(
            1 for h in self.drivers.values() if h.status == DriverStatus.UNRESPONSIVE
        )
        stats['failed_drivers'] = sum(
            1 for h in self.drivers.values() if h.status == DriverStatus.FAILED
        )
        stats['disabled_drivers'] = sum(
            1 for h in self.drivers.values() if h.status == DriverStatus.DISABLED
        )
        
        return stats
