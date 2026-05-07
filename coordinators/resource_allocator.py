#!/usr/bin/env python3
"""
Intelligent Resource Allocator
Dynamic resource allocation and scaling system for Hledač automation
"""
import asyncio
import inspect
import os
import psutil
import logging
import json
import time
from collections import deque
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import yaml
import numpy as np
from datetime import datetime, timedelta

# Sprint 72: sklearn lazy import - don't load at module level
SKLEARN_AVAILABLE = True  # Will be verified on actual use

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Sprint F206X: Bounded pending requests to prevent memory exhaustion
MAX_PENDING_RESOURCE_REQUESTS = 1000

class ResourceType(Enum):
    """Resource types for allocation"""
    CPU = "cpu"
    MEMORY = "memory"
    GPU = "gpu"
    STORAGE = "storage"
    NETWORK = "network"

class Priority(Enum):
    """Task priority levels"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4
    EMERGENCY = 5

@dataclass
class ResourceRequest:
    """Resource request specification"""
    task_id: str
    task_name: str
    priority: Priority
    cpu_cores: float
    memory_gb: float
    gpu_memory: Optional[float] = None
    storage_gb: Optional[float] = None
    network_bandwidth: Optional[float] = None
    estimated_duration: Optional[int] = None  # seconds
    max_wait_time: Optional[int] = None  # seconds
    can_preempt: bool = False
    affinity: Optional[List[str]] = None  # preferred resources
    anti_affinity: Optional[List[str]] = None  # avoid resources

@dataclass
class ResourceCapacity:
    """Available resource capacity"""
    cpu_cores: float
    memory_gb: float
    gpu_memory: float
    storage_gb: float
    network_bandwidth: float
    cpu_usage: float  # percentage
    memory_usage: float  # percentage
    gpu_usage: float  # percentage

@dataclass
class ResourceAllocation:
    """Resource allocation record"""
    task_id: str
    allocated_resources: Dict[str, float]
    start_time: datetime
    end_time: Optional[datetime]
    actual_usage: Dict[str, float]
    efficiency_score: float

class IntelligentResourceAllocator:
    """Advanced resource allocation and scaling system"""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        # Sprint F206X: dict keyed by task_id for O(1) removal after allocation, bounded size
        self._pending_requests_dict: Dict[str, ResourceRequest] = {}
        self.active_allocations = {}
        self.completed_allocations = deque(maxlen=2000)  # M218C: bounded to prevent unbounded growth
        self.resource_history = []
        # Sprint 72: Lazy init for sklearn models
        self._prediction_model = None
        self._anomaly_detector = None
        self._scaler = None

        # M1-specific optimizations
        self.m1_optimizations = {
            'cpu_efficiency_cores': 4,
            'cpu_performance_cores': 4,
            'memory_bandwidth': 68.25,  # GB/s
            'unified_memory': True,
            'neural_engine': True
        }

        # Scaling threshnews
        self.scale_up_threshnew = self.config.get('scaling', {}).get('scale_up_threshnew', 0.8)
        self.scale_down_threshnew = self.config.get('scaling', {}).get('scale_down_threshnew', 0.3)

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load resource allocation configuration"""
        default_config = {
            'resources': {
                'max_cpu_cores': 8,
                'max_memory_gb': 8.0,
                'max_gpu_memory_gb': 8.0,
                'max_storage_gb': 500.0,
                'max_network_bandwidth': 1000.0
            },
            'allocation': {
                'default_duration': 3600,  # 1 hour
                'max_wait_time': 300,     # 5 minutes
                'preemption_enabled': True,
                'efficiency_target': 0.85
            },
            'scaling': {
                'scale_up_threshnew': 0.8,
                'scale_down_threshnew': 0.3,
                'prediction_window': 300,  # 5 minutes
                'auto_scaling_enabled': True
            },
            'optimization': {
                'm1_specific': True,
                'mlx_acceleration': True,
                'metal_optimization': True,
                'unified_memory_optimization': True
            }
        }

        if config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                default_config.update(config)

        return default_config

    def _init_prediction_model(self):
        """Initialize resource usage prediction model (lazy)."""
        if self._prediction_model is not None:
            return self._prediction_model
        try:
            from sklearn.ensemble import RandomForestRegressor
            from sklearn.multioutput import MultiOutputRegressor
            base_model = RandomForestRegressor(
                n_estimators=100,
                random_state=42,
                max_depth=10
            )
            self._prediction_model = MultiOutputRegressor(base_model)
        except ImportError:
            self._prediction_model = None
        return self._prediction_model

    @property
    def prediction_model(self):
        """Lazy property for prediction model."""
        return self._init_prediction_model()

    @property
    def anomaly_detector(self):
        """Lazy property for anomaly detector."""
        if self._anomaly_detector is None:
            try:
                from sklearn.ensemble import IsolationForest
                self._anomaly_detector = IsolationForest(contamination=0.1, random_state=42)
            except ImportError:
                self._anomaly_detector = None
        return self._anomaly_detector

    @property
    def scaler(self):
        """Lazy property for scaler."""
        if self._scaler is None:
            try:
                from sklearn.preprocessing import StandardScaler
                self._scaler = StandardScaler()
            except ImportError:
                self._scaler = None
        return self._scaler

    async def get_current_capacity(self) -> ResourceCapacity:
        """Get current system resource capacity and usage"""
        try:
            # CPU information
            cpu_count = psutil.cpu_count()
            cpu_percent = psutil.cpu_percent(interval=1)

            # Memory information
            memory = psutil.virtual_memory()

            # GPU information (M1 specific)
            gpu_memory = 0.0
            gpu_usage = 0.0
            try:
                import subprocess
                result = subprocess.run(['system_profiler', 'SPDisplaysDataType'],
                                        capture_output=True, text=True)
                if 'Metal' in result.stdout:
                    # Estimate GPU memory based on unified memory
                    gpu_memory = self.config['resources']['max_gpu_memory_gb']
                    gpu_usage = cpu_percent * 0.7  # Rough estimate
            except Exception as e:
                logger.warning(f"Could not get GPU info: {e}")

            # Storage information
            disk = psutil.disk_usage('/')

            # Network information
            network = psutil.net_io_counters()
            network_bandwidth = 1000.0  # Default to 1Gbps

            return ResourceCapacity(
                cpu_cores=cpu_count,
                memory_gb=memory.total / (1024**3),
                gpu_memory=gpu_memory,
                storage_gb=disk.total / (1024**3),
                network_bandwidth=network_bandwidth,
                cpu_usage=cpu_percent / 100.0,
                memory_usage=memory.percent / 100.0,
                gpu_usage=gpu_usage / 100.0
            )
        except Exception as e:
            logger.error(f"Error getting resource capacity: {e}")
            return ResourceCapacity(0, 0, 0, 0, 0, 0, 0, 0)

    # Sprint 55: ANE availability check
    async def can_use_ane(self) -> bool:
        """
        Rozhodne, zda je vhodné použít ANE na základě aktuální zátěže.

        Returns:
            True pokud by měl být použit ANE embedder
        """
        # Check if CoreML/ANE is available
        try:
            from hledac.universal.brain.ane_embedder import ANE_AVAILABLE
        except ImportError:
            return False

        if not ANE_AVAILABLE:
            return False

        # Get current capacity
        capacity = await self.get_current_capacity()

        # If GPU usage is low, ANE is beneficial
        # Threshold: less than 70% GPU usage means ANE can be used efficiently
        return capacity.gpu_usage < 0.7

    # Sprint 56: Dynamic concurrency recommendation based on task type
    async def get_recommended_concurrency(self, task_type: str) -> int:
        """
        Vrátí doporučenou concurrency podle typu úlohy a aktuálních zdrojů.

        Args:
            task_type: 'io' nebo 'cpu'

        Returns:
            Doporučený počet souběžných úloh
        """
        try:
            import psutil
        except ImportError:
            # Default hodnoty pokud psutil není dostupný
            return 10 if task_type == 'io' else 4

        mem = psutil.virtual_memory()

        # Base concurrency podle typu úlohy
        if task_type == 'io':
            base = 10
        else:  # cpu
            base = 4

        # Snížení podle využití paměti
        if mem.percent > 75:
            return max(1, base // 4)
        elif mem.percent > 60:
            return max(1, base // 2)
        else:
            return base

    async def request_resources(self, request: ResourceRequest) -> bool:
        """Request resource allocation for a task"""
        logger.info(f"Resource request received: {request.task_name} (Priority: {request.priority.name})")

        # Sprint F206X: Bounded pending queue - evict oldest if full
        if len(self._pending_requests_dict) >= MAX_PENDING_RESOURCE_REQUESTS:
            oldest_key = next(iter(self._pending_requests_dict))
            del self._pending_requests_dict[oldest_key]
            logger.warning(f"Pending requests queue full, evicted oldest: {oldest_key}")

        # Add to pending queue
        self._pending_requests_dict[request.task_id] = request

        # Attempt allocation
        success = await self._allocate_resources(request)
        if success:
            # Bugfix: remove from pending after successful allocation
            self._pending_requests_dict.pop(request.task_id, None)
        return success

    async def _allocate_resources(self, request: ResourceRequest) -> bool:
        """Attempt to allocate resources for a request"""
        capacity = await self.get_current_capacity()

        # Check if resources are available
        if await self._can_allocate(request, capacity):
            allocation = await self._create_allocation(request, capacity)
            if allocation:
                self.active_allocations[request.task_id] = allocation
                logger.info(f"Resources allocated for {request.task_name}")
                return True

        # If immediate allocation failed, try preemptive allocation
        if request.can_preempt and request.priority.value >= Priority.HIGH.value:
            return await self._preempt_and_allocate(request)

        logger.warning(f"Could not allocate resources for {request.task_name}")
        return False

    async def _can_allocate(self, request: ResourceRequest, capacity: ResourceCapacity) -> bool:
        """Check if resources can be allocated"""
        available_cpu = capacity.cpu_cores * (1 - capacity.cpu_usage)
        available_memory = capacity.memory_gb * (1 - capacity.memory_usage)
        available_gpu = capacity.gpu_memory * (1 - capacity.gpu_usage)

        return (
            request.cpu_cores <= available_cpu and
            request.memory_gb <= available_memory and
            (request.gpu_memory is None or request.gpu_memory <= available_gpu)
        )

    async def _create_allocation(self, request: ResourceRequest, capacity: ResourceCapacity) -> Optional[ResourceAllocation]:
        """Create resource allocation"""
        try:
            allocated_resources = {
                'cpu_cores': request.cpu_cores,
                'memory_gb': request.memory_gb
            }

            if request.gpu_memory:
                allocated_resources['gpu_memory'] = request.gpu_memory

            allocation = ResourceAllocation(
                task_id=request.task_id,
                allocated_resources=allocated_resources,
                start_time=datetime.now(),
                end_time=None,
                actual_usage={},
                efficiency_score=0.0
            )

            # Apply M1 optimizations if enabled
            if self.config['optimization']['m1_specific']:
                await self._apply_m1_optimizations(allocation)

            return allocation

        except Exception as e:
            logger.error(f"Error creating allocation: {e}")
            return None

    async def _apply_m1_optimizations(self, allocation: ResourceAllocation):
        """Apply M1-specific optimizations"""
        if self.config['optimization']['mlx_acceleration']:
                os.environ['MLX_ACCELERATION'] = '1'

        if self.config['optimization']['metal_optimization']:
                os.environ['METAL_DEVICE_WRAPPER_TYPE'] = '1'

        if self.config['optimization']['unified_memory_optimization']:
            # Optimize for unified memory architecture
            cpu_cores = allocation.allocated_resources.get('cpu_cores', 1)
            os.environ['OMP_NUM_THREADS'] = str(int(cpu_cores))

    async def _preempt_and_allocate(self, request: ResourceRequest) -> bool:
        """Preempt lower priority tasks to free resources"""
        preemptible_tasks = [
            (task_id, alloc) for task_id, alloc in self.active_allocations.items()
            if alloc.efficiency_score < self.scale_down_threshnew
        ]

        # Sort by efficiency score (lowest first)
        preemptible_tasks.sort(key=lambda x: x[1].efficiency_score)

        for task_id, allocation in preemptible_tasks:
            logger.info(f"Preempting task {task_id} for high priority task {request.task_name}")
            await self.release_resources(task_id)

            # Try allocation again
            if await self._allocate_resources(request):
                return True

        return False

    async def release_resources(self, task_id: str):
        """Release allocated resources"""
        if task_id in self.active_allocations:
                allocation = self.active_allocations[task_id]
                allocation.end_time = datetime.now()

            # Calculate efficiency score
                duration = (allocation.end_time - allocation.start_time).total_seconds()
                if duration > 0:
                    allocation.efficiency_score = min(1.0, allocation.allocated_resources.get('cpu_cores', 1) / duration)

                self.completed_allocations.append(allocation)  # M218C: deque auto-evicts oldest
                del self.active_allocations[task_id]

                logger.info(f"Resources released for task {task_id}")

    async def monitor_and_optimize(self):
        """Monitor resource usage and optimize allocations"""
        while True:
            try:
                # Update resource history
                capacity = await self.get_current_capacity()
                self.resource_history.append({
                    'timestamp': datetime.now(),
                    'cpu_usage': capacity.cpu_usage,
                    'memory_usage': capacity.memory_usage,
                    'gpu_usage': capacity.gpu_usage,
                    'active_allocations': len(self.active_allocations)
                })

                # Keep history limited to last 1000 entries
                if len(self.resource_history) > 1000:
                        self.resource_history = self.resource_history[-1000:]

                # Detect anomalies
                if len(self.resource_history) > 50:
                        await self._detect_and_handle_anomalies()

                # Auto-scale if enabled
                if self.config['scaling']['auto_scaling_enabled']:
                        await self._auto_scale()

                # Optimize active allocations
                await self._optimize_active_allocations()

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.error(f"Error in resource monitoring: {e}")
                await asyncio.sleep(60)

    async def _detect_and_handle_anomalies(self):
        """Detect and handle resource usage anomalies"""
        try:
            # Prepare data for anomaly detection
            recent_data = []
            for entry in self.resource_history[-50:]:
                recent_data.append([
                    entry['cpu_usage'],
                    entry['memory_usage'],
                    entry['gpu_usage']
                ])

            if len(recent_data) > 10:
                # Detect anomalies
                anomalies = self.anomaly_detector.fit_predict(recent_data)

                # Handle anomalies
                for i, is_anomaly in enumerate(anomalies):
                    if is_anomaly == -1:  # Anomaly detected
                        logger.warning(f"Resource usage anomaly detected at index {i}")
                        await self._handle_resource_anomaly(i)

        except Exception as e:
            logger.error(f"Error in anomaly detection: {e}")

    async def _handle_resource_anomaly(self, history_index: int):
        """Handle detected resource anomaly"""
        # Get the anomalous data point
        if history_index < len(self.resource_history):
            anomaly_data = self.resource_history[-(50 - history_index)]

            # If CPU usage is too high, consider preempting low-priority tasks
            if anomaly_data['cpu_usage'] > self.scale_up_threshnew:
                low_priority_tasks = [
                    task_id for task_id, alloc in self.active_allocations.items()
                    if alloc.efficiency_score < 0.5
                ]

                for task_id in low_priority_tasks[:2]:  # Preempt up to 2 tasks
                    logger.warning(f"Preempting task {task_id} due to resource anomaly")
                    await self.release_resources(task_id)

    async def _auto_scale(self):
        """Automatic scaling based on resource usage"""
        capacity = await self.get_current_capacity()

        # Scale up if resources are highly utilized
        if (capacity.cpu_usage > self.scale_up_threshnew or
            capacity.memory_usage > self.scale_up_threshnew):

            logger.info("High resource utilization detected, considering scale-up")
            await self._scale_up_resources()

        # Scale down if resources are underutilized
        elif (capacity.cpu_usage < self.scale_down_threshnew and
                capacity.memory_usage < self.scale_down_threshnew):

            logger.info("Low resource utilization detected, considering scale-down")
            await self._scale_down_resources()

    async def _scale_up_resources(self):
        """Scale up resource allocation"""
        # Optimize for higher performance on M1
        if self.config['optimization']['m1_specific']:
            # Enable performance cores
            os.environ['CPU_PERFORMANCE_MODE'] = '1'

            # Increase memory allocation efficiency
            os.environ['MEMORY_EFFICIENCY_MODE'] = 'performance'

    async def _scale_down_resources(self):
        """Scale down resource allocation"""
        # Optimize for efficiency on M1
        if self.config['optimization']['m1_specific']:
            # Use efficiency cores
            os.environ['CPU_PERFORMANCE_MODE'] = '0'

            # Optimize memory usage
            os.environ['MEMORY_EFFICIENCY_MODE'] = 'efficiency'

    async def _optimize_active_allocations(self):
        """Optimize active resource allocations"""
        for task_id, allocation in self.active_allocations.items():
            # Update actual usage based on current system state
            capacity = await self.get_current_capacity()

            allocation.actual_usage = {
                'cpu_cores': capacity.cpu_usage * allocation.allocated_resources.get('cpu_cores', 1),
                'memory_gb': capacity.memory_usage * allocation.allocated_resources.get('memory_gb', 1)
            }

            # Calculate efficiency score
            allocated_cpu = allocation.allocated_resources.get('cpu_cores', 1)
            used_cpu = allocation.actual_usage.get('cpu_cores', 0)

            if allocated_cpu > 0:
                    allocation.efficiency_score = min(1.0, used_cpu / allocated_cpu)

    def get_allocation_statistics(self) -> Dict[str, Any]:
        """Get resource allocation statistics"""
        stats = {
            'total_requests': len(self._pending_requests_dict) + len(self.active_allocations) + len(self.completed_allocations),
            'pending_requests': len(self._pending_requests_dict),
            'active_allocations': len(self.active_allocations),
            'completed_allocations': len(self.completed_allocations),
            'average_efficiency': 0.0,
            'resource_utilization': {}
        }

        if self.completed_allocations:
                total_efficiency = sum(alloc.efficiency_score for alloc in self.completed_allocations)
                stats['average_efficiency'] = total_efficiency / len(self.completed_allocations)

        # M218C telemetry
        stats['completed_allocations_maxlen'] = getattr(self.completed_allocations, 'maxlen', None)
        stats['completed_allocations_current_len'] = len(self.completed_allocations)

        if self.resource_history:
            latest = self.resource_history[-1]
            stats['resource_utilization'] = {
                'cpu_usage': latest['cpu_usage'],
                'memory_usage': latest['memory_usage'],
                'gpu_usage': latest['gpu_usage']
            }

        return stats

    def export_allocation_report(self, filepath: str):
        """Export detailed allocation report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'statistics': self.get_allocation_statistics(),
            'active_allocations': [
                {
                    'task_id': alloc.task_id,
                    'allocated_resources': alloc.allocated_resources,
                    'start_time': alloc.start_time.isoformat(),
                    'efficiency_score': alloc.efficiency_score
                }
                for alloc in self.active_allocations.values()
            ],
            'recent_allocations': [
                {
                    'task_id': alloc.task_id,
                    'allocated_resources': alloc.allocated_resources,
                    'start_time': alloc.start_time.isoformat(),
                    'end_time': alloc.end_time.isoformat() if alloc.end_time else None,
                    'efficiency_score': alloc.efficiency_score
                }
                for alloc in list(self.completed_allocations)[-20:]  # Last 20 allocations
            ]
        }

        with open(filepath, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Allocation report exported to {filepath}")

# Resource-aware task scheduler
class ResourceAwareScheduler:
    """Task scheduler with resource awareness"""

    def __init__(self, allocator: IntelligentResourceAllocator):
        self.allocator = allocator
        self.task_queue = []
        self.running_tasks = {}

    async def schedule_task(self,
                        task_id: str,
                        task_func: callable,
                        resource_request: ResourceRequest) -> bool:
        """Schedule a task with resource requirements"""
        logger.info(f"Scheduling task: {task_id}")

        # Request resources
        if await self.allocator.request_resources(resource_request):
            # Execute task
            asyncio.create_task(self._execute_task(task_id, task_func), name=f"resource_allocator:execute_task:{task_id}")
            return True
        else:
            logger.error(f"Failed to schedule task {task_id}: insufficient resources")
            return False

    async def _execute_task(self, task_id: str, task_func: callable):
        """Execute a task with allocated resources"""
        try:
            logger.info(f"Executing task {task_id}")

            # Execute the task
            result = await task_func()

            logger.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")

        finally:
            # Release resources
            await self.allocator.release_resources(task_id)

# Parallel execution optimizer
class ParallelExecutionOptimizer:
    """Optimizer for parallel task execution"""

    def __init__(self, allocator: IntelligentResourceAllocator):
        self.allocator = allocator
        self.execution_history = []

    async def optimize_parallel_execution(self,
                                        tasks: List[tuple],
                                        max_parallel_tasks: Optional[int] = None) -> List[Any]:
        """Execute tasks in parallel with optimal resource allocation"""
        if not max_parallel_tasks:
            # Determine optimal parallelism based on available resources
            capacity = await self.allocator.get_current_capacity()
            max_parallel_tasks = min(
                int(capacity.cpu_cores),
                int(capacity.memory_gb / 2),  # Assume 2GB per task minimum
                8  # Max parallelism limit
            )

        logger.info(f"Executing {len(tasks)} tasks with max parallelism: {max_parallel_tasks}")

        # Create task batches
        task_batches = [
            tasks[i:i + max_parallel_tasks]
            for i in range(0, len(tasks), max_parallel_tasks)
        ]

        results = []

        for batch_idx, batch in enumerate(task_batches):
            logger.info(f"Executing batch {batch_idx + 1}/{len(task_batches)} with {len(batch)} tasks")

            # Create resource requests for batch
            batch_tasks = []
            for task_data in batch:
                if isinstance(task_data, tuple) and len(task_data) == 2:
                        task_func, task_args = task_data
                        resource_request = ResourceRequest(
                        task_id=f"batch_{batch_idx}_task_{len(batch_tasks)}",
                        task_name=f"Batch {batch_idx} Task {len(batch_tasks)}",
                        priority=Priority.MEDIUM,
                        cpu_cores=1.0,
                        memory_gb=2.0,
                        estimated_duration=300
                    )
                        batch_tasks.append((task_func, task_args, resource_request))

            # Execute batch in parallel
            batch_results = await self._execute_parallel_batch(batch_tasks)
            results.extend(batch_results)

        return results

    async def _execute_parallel_batch(self, batch_tasks: List[tuple]) -> List[Any]:
        """Execute a batch of tasks in parallel"""
        async def execute_single_task(task_func, task_args, resource_request):
            # Request resources
            if await self.allocator.request_resources(resource_request):
                try:
                    # Execute task
                    if inspect.iscoroutinefunction(task_func):
                        result = await task_func(task_args)
                    else:
                        result = task_func(task_args)
                    return result
                finally:
                    # Release resources
                    await self.allocator.release_resources(resource_request.task_id)
            else:
                    return None

        # Execute all tasks in the batch concurrently
        tasks = [
            execute_single_task(task_func, task_args, resource_request)
            for task_func, task_args, resource_request in batch_tasks
        ]

        return await asyncio.gather(*tasks, return_exceptions=True)

# CLI interface
async def main():
    """Main function for resource allocator testing"""
    allocator = IntelligentResourceAllocator()
    scheduler = ResourceAwareScheduler(allocator)
    optimizer = ParallelExecutionOptimizer(allocator)

    # Start monitoring
    monitoring_task = asyncio.create_task(allocator.monitor_and_optimize(), name="resource_allocator:monitor")

    # Example task
    async def example_task(task_args):
        print(f"Executing task with args: {task_args}")
        await asyncio.sleep(2)
        return f"Task completed: {task_args}"

    # Schedule example tasks
    tasks = [
        (example_task, f"task_{i}")
        for i in range(5)
    ]

    # Execute with optimization
    results = await optimizer.optimize_parallel_execution(tasks)

    print(f"Results: {results}")

    # Export report
    allocator.export_allocation_report("resource_allocation_report.json")

    # Clean up
    monitoring_task.cancel()

if __name__ == "__main__":
        asyncio.run(main())