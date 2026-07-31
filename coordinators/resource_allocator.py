"""
Intelligent Resource Allocator — DEPRECATED for GC/backpressure/AIMD
================================================================

NOTE: GC/backpressure/AIMD functionality has moved to:
    coordinators.resource.resource_coordinator

This module still contains the full sklearn-based prediction model
and ResourceAwareScheduler — these are NOT deprecated.

Import GC/backpressure/AIMD from:
    from hledac.universal.coordinators.resource import gc_collect, BackpressureMonitor, AIMDController
"""

import warnings

warnings.warn(
    "coordinators.resource_allocator: GC/backpressure/AIMD are deprecated here. "
    "Import from coordinators.resource.resource_coordinator instead.",
    DeprecationWarning,
    stacklevel=2,
)
import asyncio
import logging
import os
import subprocess
import time as time_module
from collections import deque

# M1 8GB safe default: 500 × ~200-400B per ResourceAllocation(frozen Struct) ≈ 100-200KB
# Makes it configurable for larger machines via env var
_MAX_COMPLETED_ALLOCATIONS_DEFAULT = 500
_MAX_COMPLETED_ALLOCATIONS_ENV = "HLEDAC_MAX_COMPLETED_ALLOCATIONS"
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import msgspec
import yaml

from hledac.universal.core.psutil_shim import psutil
from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str

SKLEARN_AVAILABLE = True
logger = logging.getLogger(__name__)
MAX_PENDING_RESOURCE_REQUESTS = 1000

class CapacitySnapshot(msgspec.Struct, frozen=True, gc=False):
    """Immutable snapshot of resource capacity with TTL tracking."""
    cpu_percent: float
    gpu_memory: float
    gpu_usage: float
    metal_available: bool
    sampled_at_monotonic: float

class _ResourceCapacitySampler:
    """
    Async-owned resource capacity sampler with TTL caching.

    Offloads blocking psutil.cpu_percent(interval=1) and system_profiler
    calls from async hot paths via asyncio.to_thread.
    """
    _CPU_TTL_S = 3.0
    _METAL_TTL_S = 300.0
    __slots__ = ('_cpu_cache', '_cpu_lock', '_metal_cache', '_metal_cache_time', '_metal_lock')

    def __init__(self) -> None:
        self._cpu_lock = asyncio.Lock()
        self._metal_lock = asyncio.Lock()
        self._cpu_cache: CapacitySnapshot | None = None
        self._metal_cache: bool | None = None
        self._metal_cache_time: float = 0.0

    def _get_cpu_sync(self) -> tuple[float, float, float]:
        """
        Blocking CPU/memory read via psutil.
        MUST be called via asyncio.to_thread, never directly from event loop.
        Returns (cpu_percent, gpu_memory, gpu_usage).
        Uses interval=0.0 for non-blocking CPU measurement.
        """
        cpu_percent = psutil.cpu_percent(interval=0.0)
        psutil.virtual_memory()
        gpu_memory = 0.0
        gpu_usage = cpu_percent * 0.7
        return (cpu_percent, gpu_memory, gpu_usage)

    def _get_metal_sync(self) -> bool:
        """
        Blocking system_profiler call for Metal availability.
        MUST be called via asyncio.to_thread.
        """
        try:
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, timeout=5)
            return 'Metal' in result.stdout
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return False

    async def sample(self) -> CapacitySnapshot:
        """
        Get capacity snapshot with per-field TTL caching.

        CPU/performance metrics: short TTL (3s).
        Metal availability: long TTL (300s) since it rarely changes.
        All blocking I/O offloaded via asyncio.to_thread.
        """
        now = time_module.monotonic()
        cached = self._cpu_cache
        if cached is not None and now - cached.sampled_at_monotonic < self._CPU_TTL_S:
            return cached
        async with self._cpu_lock:
            now = time_module.monotonic()
            if self._cpu_cache is not None and now - self._cpu_cache.sampled_at_monotonic < self._CPU_TTL_S:
                return self._cpu_cache
            cpu_percent, gpu_memory, gpu_usage = await asyncio.to_thread(self._get_cpu_sync)
            metal_available = await self._get_metal_with_cache(now)
            self._cpu_cache = CapacitySnapshot(cpu_percent=cpu_percent, gpu_memory=gpu_memory, gpu_usage=gpu_usage, metal_available=metal_available, sampled_at_monotonic=now)
            return self._cpu_cache

    async def _get_metal_with_cache(self, now: float) -> bool:
        """Get Metal availability with long TTL caching."""
        if self._metal_cache is not None and now - self._metal_cache_time < self._METAL_TTL_S:
            return self._metal_cache
        async with self._metal_lock:
            if self._metal_cache is not None and now - self._metal_cache_time < self._METAL_TTL_S:
                return self._metal_cache
            metal_available = await asyncio.to_thread(self._get_metal_sync)
            self._metal_cache = metal_available
            self._metal_cache_time = now
            return metal_available

class ResourceType(Enum):
    """Resource types for allocation"""
    CPU = 'cpu'
    MEMORY = 'memory'
    GPU = 'gpu'
    STORAGE = 'storage'
    NETWORK = 'network'

class Priority(Enum):
    """Task priority levels"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4
    EMERGENCY = 5

class ResourceRequest(msgspec.Struct, gc=False):
    """Resource request specification"""
    task_id: str
    task_name: str
    priority: Priority
    cpu_cores: float
    memory_gb: float
    gpu_memory: float | None = None
    storage_gb: float | None = None
    network_bandwidth: float | None = None
    estimated_duration: int | None = None
    max_wait_time: int | None = None
    can_preempt: bool = False
    affinity: list[str] | None = None
    anti_affinity: list[str] | None = None

class ResourceCapacity(msgspec.Struct, frozen=True, gc=False):
    """Available resource capacity"""
    cpu_cores: float
    memory_gb: float
    gpu_memory: float
    storage_gb: float
    network_bandwidth: float
    cpu_usage: float
    memory_usage: float
    gpu_usage: float

class ResourceAllocation(msgspec.Struct, frozen=True, gc=False):
    """Resource allocation record"""
    task_id: str
    allocated_resources: dict[str, float]
    start_time: datetime
    end_time: datetime | None
    actual_usage: dict[str, float]
    efficiency_score: float

class IntelligentResourceAllocator:
    """Advanced resource allocation and scaling system"""
    __slots__ = ('_anomaly_detector', '_capacity_sampler', '_pending_requests_dict', '_prediction_model', '_scaler', 'active_allocations', 'completed_allocations', 'config', 'm1_optimizations', 'resource_history', 'scale_down_threshnew', 'scale_up_threshnew')

    def __init__(self, config_path: str | None=None) -> None:
        self.config = self._load_config(config_path or '')
        self._pending_requests_dict: dict[str, ResourceRequest] = {}
        self.active_allocations = {}
        _max_completed = int(os.environ.get(
            _MAX_COMPLETED_ALLOCATIONS_ENV, _MAX_COMPLETED_ALLOCATIONS_DEFAULT
        ))
        self.completed_allocations = deque(maxlen=_max_completed)
        self.resource_history = []
        self._prediction_model = None
        self._anomaly_detector = None
        self._scaler = None
        self.m1_optimizations = {'cpu_efficiency_cores': 4, 'cpu_performance_cores': 4, 'memory_bandwidth': 68.25, 'unified_memory': True, 'neural_engine': True}
        self._capacity_sampler = _ResourceCapacitySampler()
        self.scale_up_threshnew = self.config.get('scaling', {}).get('scale_up_threshnew', 0.8)
        self.scale_down_threshnew = self.config.get('scaling', {}).get('scale_down_threshnew', 0.3)

    def _load_config(self, config_path: str) -> dict[str, Any]:
        """Load resource allocation configuration"""
        default_config = {'resources': {'max_cpu_cores': 8, 'max_memory_gb': 8.0, 'max_gpu_memory_gb': 8.0, 'max_storage_gb': 500.0, 'max_network_bandwidth': 1000.0}, 'allocation': {'default_duration': 3600, 'max_wait_time': 300, 'preemption_enabled': True, 'efficiency_target': 0.85}, 'scaling': {'scale_up_threshnew': 0.8, 'scale_down_threshnew': 0.3, 'prediction_window': 300, 'auto_scaling_enabled': True}, 'optimization': {'m1_specific': True, 'mlx_acceleration': True, 'metal_optimization': True, 'unified_memory_optimization': True}}
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
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
            base_model = RandomForestRegressor(n_estimators=100, random_state=42, max_depth=10)
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
        """
        Get current system resource capacity and usage.

        Offloads blocking psutil/system_profiler calls via _ResourceCapacitySampler.
        Fail-soft: returns default ResourceCapacity on any error.
        F265H: All blocking psutil calls now offloaded via asyncio.to_thread.
        """
        try:
            snapshot = await self._capacity_sampler.sample()

            def _read_sysinfo_sync() -> tuple[Any, Any, int]:
                cpu_count = psutil.cpu_count()
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                psutil.net_io_counters()
                return (memory, disk, cpu_count)
            memory, disk, cpu_count = await asyncio.to_thread(_read_sysinfo_sync)
            network_bandwidth = 1000.0
            return ResourceCapacity(cpu_cores=cpu_count, memory_gb=memory.total / 1024 ** 3, gpu_memory=snapshot.gpu_memory, storage_gb=disk.total / 1024 ** 3, network_bandwidth=network_bandwidth, cpu_usage=snapshot.cpu_percent / 100.0, memory_usage=memory.percent / 100.0, gpu_usage=snapshot.gpu_usage / 100.0)
        except Exception as e:
            logger.error(f'Error getting resource capacity: {e}')
            return ResourceCapacity(0, 0, 0, 0, 0, 0, 0, 0)

    async def can_use_ane(self) -> bool:
        """
        Rozhodne, zda je vhodné použít ANE na základě aktuální zátěže.

        Returns:
            True pokud by měl být použit ANE embedder
        """
        try:
            from hledac.universal.brain.ane_embedder import ANE_AVAILABLE
        except ImportError:
            return False
        if not ANE_AVAILABLE:
            return False
        capacity = await self.get_current_capacity()
        return capacity.gpu_usage < 0.7

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
            return 10 if task_type == 'io' else 4
        mem = psutil.virtual_memory()
        base = 10 if task_type == 'io' else 4
        if mem.percent > 75:
            return max(1, base // 4)
        elif mem.percent > 60:
            return max(1, base // 2)
        else:
            return base

    async def request_resources(self, request: ResourceRequest) -> bool:
        """Request resource allocation for a task"""
        logger.info(f'Resource request received: {request.task_name} (Priority: {request.priority.name})')
        if len(self._pending_requests_dict) >= MAX_PENDING_RESOURCE_REQUESTS:
            oldest_key = next(iter(self._pending_requests_dict))
            del self._pending_requests_dict[oldest_key]
            logger.warning(f'Pending requests queue full, evicted oldest: {oldest_key}')
        self._pending_requests_dict[request.task_id] = request
        success = await self._allocate_resources(request)
        if success:
            self._pending_requests_dict.pop(request.task_id, None)
        return success

    async def _allocate_resources(self, request: ResourceRequest) -> bool:
        """Attempt to allocate resources for a request"""
        capacity = await self.get_current_capacity()
        if await self._can_allocate(request, capacity):
            allocation = await self._create_allocation(request, capacity)
            if allocation:
                self.active_allocations[request.task_id] = allocation
                logger.info(f'Resources allocated for {request.task_name}')
                return True
        if request.can_preempt and request.priority.value >= Priority.HIGH.value:
            return await self._preempt_and_allocate(request)
        logger.warning(f'Could not allocate resources for {request.task_name}')
        return False

    async def _can_allocate(self, request: ResourceRequest, capacity: ResourceCapacity) -> bool:
        """Check if resources can be allocated"""
        available_cpu = capacity.cpu_cores * (1 - capacity.cpu_usage)
        available_memory = capacity.memory_gb * (1 - capacity.memory_usage)
        available_gpu = capacity.gpu_memory * (1 - capacity.gpu_usage)
        return request.cpu_cores <= available_cpu and request.memory_gb <= available_memory and (request.gpu_memory is None or request.gpu_memory <= available_gpu)

    async def _create_allocation(self, request: ResourceRequest, capacity: ResourceCapacity) -> ResourceAllocation | None:
        """Create resource allocation"""
        try:
            allocated_resources = {'cpu_cores': request.cpu_cores, 'memory_gb': request.memory_gb}
            if request.gpu_memory:
                allocated_resources['gpu_memory'] = request.gpu_memory
            allocation = ResourceAllocation(task_id=request.task_id, allocated_resources=allocated_resources, start_time=datetime.now(UTC), end_time=None, actual_usage={}, efficiency_score=0.0)
            if self.config['optimization']['m1_specific']:
                await self._apply_m1_optimizations(allocation)
            return allocation
        except Exception as e:
            logger.error(f'Error creating allocation: {e}')
            return None

    async def _apply_m1_optimizations(self, allocation: ResourceAllocation) -> None:
        """Apply M1-specific optimizations"""
        if self.config['optimization']['mlx_acceleration']:
            os.environ['MLX_ACCELERATION'] = '1'
        if self.config['optimization']['metal_optimization']:
            os.environ['METAL_DEVICE_WRAPPER_TYPE'] = '1'
        if self.config['optimization']['unified_memory_optimization']:
            cpu_cores = allocation.allocated_resources.get('cpu_cores', 1)
            os.environ['OMP_NUM_THREADS'] = str(int(cpu_cores))

    async def _preempt_and_allocate(self, request: ResourceRequest) -> bool:
        """Preempt lower priority tasks to free resources"""
        preemptible_tasks = [(task_id, alloc) for task_id, alloc in self.active_allocations.items() if alloc.efficiency_score < self.scale_down_threshnew]
        preemptible_tasks.sort(key=lambda x: x[1].efficiency_score)
        for task_id, _allocation in preemptible_tasks:
            logger.info(f'Preempting task {task_id} for high priority task {request.task_name}')
            await self.release_resources(task_id)
            if await self._allocate_resources(request):
                return True
        return False

    async def release_resources(self, task_id: str) -> None:
        """Release allocated resources"""
        if task_id in self.active_allocations:
            allocation = self.active_allocations[task_id]
            allocation.end_time = datetime.now(UTC)
            duration = (allocation.end_time - allocation.start_time).total_seconds()
            if duration > 0:
                allocation.efficiency_score = min(1.0, allocation.allocated_resources.get('cpu_cores', 1) / duration)
            self.completed_allocations.append(allocation)
            del self.active_allocations[task_id]
            logger.info(f'Resources released for task {task_id}')

    async def monitor_and_optimize(self) -> None:
        """Monitor resource usage and optimize allocations"""
        while True:
            try:
                capacity = await self.get_current_capacity()
                self.resource_history.append({'timestamp': datetime.now(UTC), 'cpu_usage': capacity.cpu_usage, 'memory_usage': capacity.memory_usage, 'gpu_usage': capacity.gpu_usage, 'active_allocations': len(self.active_allocations)})
                if len(self.resource_history) > 1000:
                    self.resource_history = self.resource_history[-1000:]
                if len(self.resource_history) > 50:
                    await self._detect_and_handle_anomalies()
                if self.config['scaling']['auto_scaling_enabled']:
                    await self._auto_scale()
                await self._optimize_active_allocations()
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f'Error in resource monitoring: {e}')
                await asyncio.sleep(60)

    async def _detect_and_handle_anomalies(self) -> None:
        """Detect and handle resource usage anomalies"""
        try:
            recent_data = []
            for entry in self.resource_history[-50:]:
                recent_data.append([entry['cpu_usage'], entry['memory_usage'], entry['gpu_usage']])
            if len(recent_data) > 10:
                anomalies = self.anomaly_detector.fit_predict(recent_data)
                for i, is_anomaly in enumerate(anomalies):
                    if is_anomaly == -1:
                        logger.warning(f'Resource usage anomaly detected at index {i}')
                        await self._handle_resource_anomaly(i)
        except Exception as e:
            logger.error(f'Error in anomaly detection: {e}')

    async def _handle_resource_anomaly(self, history_index: int) -> None:
        """Handle detected resource anomaly"""
        if history_index < len(self.resource_history):
            anomaly_data = self.resource_history[-(50 - history_index)]
            if anomaly_data['cpu_usage'] > self.scale_up_threshnew:
                low_priority_tasks = [task_id for task_id, alloc in self.active_allocations.items() if alloc.efficiency_score < 0.5]
                for task_id in low_priority_tasks[:2]:
                    logger.warning(f'Preempting task {task_id} due to resource anomaly')
                    await self.release_resources(task_id)

    async def _auto_scale(self) -> None:
        """Automatic scaling based on resource usage"""
        capacity = await self.get_current_capacity()
        if capacity.cpu_usage > self.scale_up_threshnew or capacity.memory_usage > self.scale_up_threshnew:
            logger.info('High resource utilization detected, considering scale-up')
            await self._scale_up_resources()
        elif capacity.cpu_usage < self.scale_down_threshnew and capacity.memory_usage < self.scale_down_threshnew:
            logger.info('Low resource utilization detected, considering scale-down')
            await self._scale_down_resources()

    async def _scale_up_resources(self) -> None:
        """Scale up resource allocation"""
        if self.config['optimization']['m1_specific']:
            os.environ['CPU_PERFORMANCE_MODE'] = '1'
            os.environ['MEMORY_EFFICIENCY_MODE'] = 'performance'

    async def _scale_down_resources(self) -> None:
        """Scale down resource allocation"""
        if self.config['optimization']['m1_specific']:
            os.environ['CPU_PERFORMANCE_MODE'] = '0'
            os.environ['MEMORY_EFFICIENCY_MODE'] = 'efficiency'

    async def _optimize_active_allocations(self) -> None:
        """Optimize active resource allocations."""
        # ISSUE-2b: get_current_capacity() is expensive (psutil via to_thread) —
        # call ONCE before the loop, not per-allocation.
        capacity = await self.get_current_capacity()
        for _task_id, allocation in self.active_allocations.items():
            allocation.actual_usage = {'cpu_cores': capacity.cpu_usage * allocation.allocated_resources.get('cpu_cores', 1), 'memory_gb': capacity.memory_usage * allocation.allocated_resources.get('memory_gb', 1)}
            allocated_cpu = allocation.allocated_resources.get('cpu_cores', 1)
            used_cpu = allocation.actual_usage.get('cpu_cores', 0)
            if allocated_cpu > 0:
                allocation.efficiency_score = min(1.0, used_cpu / allocated_cpu)

    def get_allocation_statistics(self) -> dict[str, Any]:
        """Get resource allocation statistics"""
        stats = {'total_requests': len(self._pending_requests_dict) + len(self.active_allocations) + len(self.completed_allocations), 'pending_requests': len(self._pending_requests_dict), 'active_allocations': len(self.active_allocations), 'completed_allocations': len(self.completed_allocations), 'average_efficiency': 0.0, 'resource_utilization': {}}
        if self.completed_allocations:
            total_efficiency = sum(alloc.efficiency_score for alloc in self.completed_allocations)
            stats['average_efficiency'] = total_efficiency / len(self.completed_allocations)
        stats['completed_allocations_maxlen'] = getattr(self.completed_allocations, 'maxlen', None)
        stats['completed_allocations_current_len'] = len(self.completed_allocations)
        if self.resource_history:
            latest = self.resource_history[-1]
            stats['resource_utilization'] = {'cpu_usage': latest['cpu_usage'], 'memory_usage': latest['memory_usage'], 'gpu_usage': latest['gpu_usage']}
        return stats

    def export_allocation_report(self, filepath: str) -> None:
        """Export detailed allocation report"""
        report = {'timestamp': datetime.now(UTC).isoformat(), 'statistics': self.get_allocation_statistics(), 'active_allocations': [{'task_id': alloc.task_id, 'allocated_resources': alloc.allocated_resources, 'start_time': alloc.start_time.isoformat(), 'efficiency_score': alloc.efficiency_score} for alloc in self.active_allocations.values()], 'recent_allocations': [{'task_id': alloc.task_id, 'allocated_resources': alloc.allocated_resources, 'start_time': alloc.start_time.isoformat(), 'end_time': alloc.end_time.isoformat() if alloc.end_time else None, 'efficiency_score': alloc.efficiency_score} for alloc in list(self.completed_allocations)[-20:]]}
        with open(filepath, 'w') as f:
            f.write(_msgspec_dumps_str(report, indent=2))
        logger.info(f'Allocation report exported to {filepath}')

class ResourceAwareScheduler:
    """Task scheduler with resource awareness"""
    __slots__ = ('_shutdown_event', '_tasks', 'allocator', 'task_queue')

    def __init__(self, allocator: IntelligentResourceAllocator) -> None:
        self.allocator = allocator
        self.task_queue = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._shutdown_event: asyncio.Event | None = None

    @property
    def active_task_count(self) -> int:
        """Number of currently running tasks"""
        return len(self._tasks)

    async def schedule_task(self, task_id: str, task_func: callable, resource_request: ResourceRequest) -> bool:
        """Schedule a task with resource requirements"""
        if self._shutdown_event is not None and self._shutdown_event.is_set():
            logger.warning(f'Cannot schedule task {task_id}: shutdown in progress')
            return False
        logger.info(f'Scheduling task: {task_id}')
        if not await self.allocator.request_resources(resource_request):
            logger.error(f'Failed to schedule task {task_id}: insufficient resources')
            return False
        task = safe_create_task(self._execute_task(task_id, task_func), name=f'resource_allocator:execute_task:{task_id}')

        def done_callback(t: asyncio.Task) -> None:
            self._tasks.pop(task_id, None)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None and (not isinstance(exc, asyncio.CancelledError)):
                    logger.error(f'Task {task_id} raised {exc!r}')
        task.add_done_callback(done_callback)
        self._tasks[task_id] = task
        return True

    async def _execute_task(self, task_id: str, task_func: callable) -> None:
        """Execute a task with allocated resources"""
        try:
            logger.info(f'Executing task {task_id}')
            await task_func()
            logger.info(f'Task {task_id} completed successfully')
        except asyncio.CancelledError:
            logger.info(f'Task {task_id} cancelled')
            raise
        except Exception as e:
            logger.error(f'Task {task_id} failed: {e}')
        finally:
            await self.allocator.release_resources(task_id)

    async def shutdown(self, timeout: float=30.0) -> None:
        """Graceful shutdown - wait for tasks to complete"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        self._shutdown_event.set()
        if not self._tasks:
            return
        logger.info(f'Shutting down scheduler, waiting for {len(self._tasks)} tasks')
        # ISSUE-15: asyncio.wait(ALL_COMPLETED) → asyncio.TaskGroup
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        except TimeoutError:
            pending = [t for t in self._tasks.values() if not t.done()]
        else:
            pending = []
        if pending:
            logger.warning(f'Cancelling {len(pending)} remaining tasks')
            for task in pending:
                task.cancel()
            try:
                async with asyncio.timeout(5.0):
                    await asyncio.gather(*pending, return_exceptions=True)
            except TimeoutError:
                pass
        self._tasks.clear()

async def main() -> None:
    """Main function for resource allocator testing"""
    allocator = IntelligentResourceAllocator()
    ResourceAwareScheduler(allocator)
    monitoring_task = safe_create_task(allocator.monitor_and_optimize(), name='resource_allocator:monitor')

    async def example_task(task_args) -> str:
        print(f'Executing task with args: {task_args}')
        await asyncio.sleep(2)
        return f'Task completed: {task_args}'
    tasks = [(example_task, f'task_{i}') for i in range(5)]
    results = await optimizer.optimize_parallel_execution(tasks)
    print(f'Results: {results}')
    allocator.export_allocation_report('resource_allocation_report.json')
    monitoring_task.cancel()
if __name__ == '__main__':
    asyncio.run(main())
