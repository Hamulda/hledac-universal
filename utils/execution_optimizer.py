"""
Parallel Execution Optimizer
Advanced parallel execution optimization for Hledač automation systems
"""
import asyncio
import msgspec
import inspect
import logging
import multiprocessing
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
import msgspec.json as _json
import numpy as np
import psutil
from .async_helpers import safe_create_task, safe_gather_ok, parallel
from .lru_cache import LRUCache
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
PSUTIL_AVAILABLE = True

class ExecutionStrategy(Enum):
    """Parallel execution strategies"""
    ROUND_ROBIN = 'round_robin'
    LOAD_BALANCED = 'load_balanced'
    RESOURCE_AWARE = 'resource_aware'
    PREDICTIVE = 'predictive'
    ADAPTIVE = 'adaptive'
    INTERPRETER_POOL = 'interpreter_pool'

class TaskType(Enum):
    """Task types for optimization"""
    CPU_INTENSIVE = 'cpu_intensive'
    MEMORY_INTENSIVE = 'memory_intensive'
    IO_INTENSIVE = 'io_intensive'
    NETWORK_INTENSIVE = 'network_intensive'
    MIXED = 'mixed'

class TaskMetrics(msgspec.Struct):
    """Task execution metrics"""
    task_id: str
    task_type: TaskType
    start_time: datetime
    end_time: datetime | None
    cpu_usage: float
    memory_usage: float
    execution_time: float
    success: bool
    worker_id: str | None = None
    parallel_group: str | None = None

class WorkerMetrics(msgspec.Struct):
    """Worker performance metrics"""
    worker_id: str
    cpu_cores: int
    memory_gb: float
    current_load: float
    tasks_completed: int
    average_task_time: float
    efficiency_score: float
    last_updated: datetime

class ParallelGroup(msgspec.Struct):
    """Parallel execution group"""
    group_id: str
    tasks: list[Any]
    strategy: ExecutionStrategy
    max_workers: int
    resource_allocation: dict[str, float]
    created_at: datetime

class _ConcurrencyController:
    """
    Dynamic concurrency controller based on system memory.

    Limits concurrent CPU-bound tasks based on available memory.
    Uses background monitor to adjust limit dynamically.
    """
    __slots__ = tuple(('_available', '_limit', '_lock', '_max_memory_threshold', '_monitor_task'))

    def __init__(self, max_memory_threshold_mb: int=1024):
        self._max_memory_threshold = max_memory_threshold_mb
        self._limit = 2
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        self._available = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        self._monitor_task: asyncio.Task | None = None
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """ISSUE-014 FIX: Lazily create lock in the current event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def start_monitoring(self):
        """Start the background memory monitor."""
        self._monitor_task = safe_create_task(self._monitor_loop())

    async def stop_monitoring(self):
        """Stop the background memory monitor."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def acquire(self):
        """Acquire a concurrency slot. Blocks if limit reached."""
        await self._available.acquire()

    def release(self):
        """Release a concurrency slot."""
        self._available.release()

    async def _monitor_loop(self):
        """Background loop that adjusts concurrency limit based on memory."""
        while True:
            await asyncio.sleep(5)
            try:
                mem_available = psutil.virtual_memory().available / (1024 * 1024)
            except Exception:
                mem_available = 2048
            async with self._get_lock():
                old_limit = self._limit
                new_limit = 1 if mem_available < self._max_memory_threshold else 2
                if new_limit != old_limit:
                    diff = new_limit - old_limit
                    if diff > 0:
                        for _ in range(diff):
                            self._available.release()
                    else:
                        for _ in range(-diff):
                            await self._available.acquire()
                    self._limit = new_limit

class ParallelExecutionOptimizer:
    """Advanced parallel execution optimization system"""
    MAX_PARALLEL_GROUPS = 100
    MAX_WORKER_METRICS = 16
    PARALLEL_GROUP_TTL_SECS = 3600
    __slots__ = tuple(('_concurrency_controller', '_execution_max_pending', '_execution_pending_throttled_count', '_execution_predictor', '_max_pending_ops', '_pending_semaphore', 'config', 'load_balancer', 'parallel_groups', 'resource_monitor', 'task_history', 'thread_pool', 'worker_metrics'))

    def __init__(self, config_path: str | None=None):
        if config_path is not None:
            self.config = self._load_config(config_path)
        else:
            self.config = self._load_config('')
        self.task_history = deque(maxlen=1000)
        self.worker_metrics: LRUCache[str, dict] = LRUCache(max_size=self.MAX_WORKER_METRICS)
        self.parallel_groups: LRUCache[str, dict] = LRUCache(max_size=self.MAX_PARALLEL_GROUPS)
        self._execution_predictor = None
        self.load_balancer = LoadBalancer()
        self.resource_monitor = ResourceMonitor()
        self._max_pending_ops = self._resolve_max_pending_ops()
        self._pending_semaphore: asyncio.Semaphore | None = None
        self._execution_max_pending = self._max_pending_ops
        self._execution_pending_throttled_count = 0
        self._concurrency_controller: _ConcurrencyController = _ConcurrencyController()
        self.thread_pool: ThreadPoolExecutor | None = None

    @property
    def _pending_limit(self) -> asyncio.Semaphore:
        """Lazy semaphore for bounded pending ops.

        F214OPT-D: Created on first access inside async context to avoid
        creating asyncio primitives outside a running loop.
        """
        if self._pending_semaphore is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            self._pending_semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        return self._pending_semaphore

    def _resolve_max_pending_ops(self) -> int:
        """Resolve max pending ops from env or return M1-safe default.

        F214OPT-D: M1 8GB can only handle ~4-8 concurrent tasks before Metal
        memory pressure causes OOM. Default to 4 (conservative) to leave headroom
        for the LLM itself (~2GB KV cache + activations).
        """
        try:
            raw = os.environ.get('HLEDAC_MAX_PENDING_OPS', '')
            if raw:
                val = int(raw)
                return max(1, min(val, 16))
        except (ValueError, TypeError):
            pass
        return 4

    async def _execute_with_semaphore(self, task: Callable) -> Any:
        """Execute a single task with semaphore gating.

        F214OPT-D: Wraps task execution with pending semaphore to prevent
        unbounded concurrent task creation. Tracks throttling for telemetry.

        CPU-bound work routes to Rust rayon pools via _rust_pool_dispatch().
        """
        try:
            if self._pending_limit.locked():
                self._execution_pending_throttled_count += 1
            await self._pending_limit.acquire()
            try:
                if inspect.iscoroutinefunction(task):
                    return await task()
                else:
                    return await self._run_in_executor_safe(self.thread_pool, task)
            finally:
                self._pending_limit.release()
        except asyncio.CancelledError:
            self._pending_limit.release()
            raise

    @property
    def execution_predictor(self):
        """Lazy-loaded predictor to avoid eager sklearn import (1478 modules)."""
        if self._execution_predictor is None:
            self._execution_predictor = self._init_predictor()
        return self._execution_predictor

    def _prune_parallel_groups(self) -> None:
        """Prune oldest and expired parallel groups."""
        now = time.time()
        expired = [gid for gid, data in self.parallel_groups.items() if now - data.get('ts', 0) > self.PARALLEL_GROUP_TTL_SECS]
        for gid in expired:
            self.parallel_groups.pop(gid, None)
        while len(self.parallel_groups) >= self.MAX_PARALLEL_GROUPS:
            self.parallel_groups.pop_lru()

    def _prune_worker_metrics(self) -> None:
        """Prune oldest worker metrics if over cap."""
        while len(self.worker_metrics) >= self.MAX_WORKER_METRICS:
            self.worker_metrics.pop_lru()

    def add_parallel_group(self, group_id: str, group_data: dict) -> None:
        """Add a parallel group with bounded storage and TTL."""
        group_data['ts'] = time.time()
        self.parallel_groups[group_id] = group_data
        self._prune_parallel_groups()

    def update_worker_metrics(self, worker_id: str, metrics: dict) -> None:
        """Update worker metrics with bounded storage."""
        self.worker_metrics[worker_id] = metrics
        self._prune_worker_metrics()

    def _load_config(self, config_path: str) -> dict[str, Any]:
        """Load parallel execution configuration"""
        m1_safe_thread_workers = 2
        default_config: dict[str, Any] = {'execution': {'default_strategy': ExecutionStrategy.ADAPTIVE.value, 'max_workers': m1_safe_thread_workers, 'thread_pool_size': m1_safe_thread_workers, 'task_timeout': 300, 'chunk_size': 100}, 'optimization': {'enable_prediction': True, 'enable_load_balancing': True, 'enable_resource_monitoring': True, 'm1_specific': True, 'auto_tuning': True, 'learning_rate': 0.1}, 'threshnews': {'cpu_threshnew': 0.8, 'memory_threshnew': 0.85, 'task_time_threshnew': 60, 'efficiency_threshnew': 0.7}, 'strategies': {'round_robin': {'enabled': True}, 'load_balanced': {'enabled': True}, 'resource_aware': {'enabled': True}, 'predictive': {'enabled': True}, 'adaptive': {'enabled': True}}}
        if config_path:
            import os
            if os.path.exists(config_path):
                with open(config_path) as f:
                    import yaml
                    config = yaml.safe_load(f)
                    default_config.update(config)
        return default_config

    def _init_predictor(self):
        """Initialize execution time predictor - lazy import to avoid eager sklearn load."""
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(n_estimators=100, random_state=42, max_depth=10)

    def _init_execution_pools(self):
        """Initialize execution pools"""
        from utils.domain_executors import get_parallel_executor
        self.thread_pool = get_parallel_executor()
        t_max = getattr(self.thread_pool, '_max_workers', '?')
        logger.info(f'Initialized execution pools - Threads: {t_max}, CPU-bound: rayon(cpu_pool_run/io_pool_run)')

    async def initialize(self) -> None:
        """Initialize async components like concurrency controller."""
        await self._concurrency_controller.start_monitoring()

    async def execute_parallel(self, tasks: list[Any], strategy: ExecutionStrategy | None=None, max_workers: int | None=None, task_type: TaskType=TaskType.MIXED) -> list[Any]:
        """Execute tasks in parallel with optimal strategy"""
        if not strategy:
            strategy = ExecutionStrategy(self.config['execution']['default_strategy'])
        if not max_workers:
            max_workers = self._determine_optimal_workers(tasks, task_type)
        logger.info(f'Executing {len(tasks)} tasks with {strategy.value} strategy and {max_workers} workers')
        start_time = time.time()
        try:
            group_id = f'parallel_group_{int(time.time())}'
            group = ParallelGroup(group_id=group_id, tasks=tasks, strategy=strategy, max_workers=max_workers, resource_allocation=await self._calculate_resource_allocation(tasks, max_workers), created_at=datetime.now(UTC))
            self.add_parallel_group(group_id, {'payload': group, 'strategy': strategy})
            if strategy == ExecutionStrategy.ROUND_ROBIN:
                results = await self._execute_round_robin(tasks, max_workers)
            elif strategy == ExecutionStrategy.LOAD_BALANCED:
                results = await self._execute_load_balanced(tasks, max_workers)
            elif strategy == ExecutionStrategy.RESOURCE_AWARE:
                results = await self._execute_resource_aware(tasks, max_workers)
            elif strategy == ExecutionStrategy.PREDICTIVE:
                results = await self._execute_predictive(tasks, max_workers)
            elif strategy == ExecutionStrategy.ADAPTIVE:
                results = await self._execute_adaptive(tasks, max_workers, task_type)
            elif strategy == ExecutionStrategy.INTERPRETER_POOL:
                results = await self._execute_interpreter_pool(tasks, max_workers)
            else:
                raise ValueError(f'Unknown execution strategy: {strategy}')
            execution_time = time.time() - start_time
            logger.info(f'Parallel execution completed in {execution_time:.2f} seconds')
            await self._record_execution_metrics(group_id, execution_time, len(tasks))
            return results
        except Exception as e:
            logger.error(f'Error in parallel execution: {e}')
            raise

    def _determine_optimal_workers(self, tasks: list[Any], task_type: TaskType) -> int:
        """Determine optimal number of workers based on task type and system resources"""
        cpu_count = multiprocessing.cpu_count()
        # E3 FIX: virtual_memory().total is stable system-wide (total RAM never changes at runtime).
        # Called only during worker-sizing decisions, not per-task — not a hot path.
        memory_gb = psutil.virtual_memory().total / 1024 ** 3
        if task_type == TaskType.CPU_INTENSIVE:
            return min(cpu_count, self.config['execution']['max_workers'])
        elif task_type == TaskType.MEMORY_INTENSIVE:
            max_memory_workers = int(memory_gb / 2)
            return min(max_memory_workers, cpu_count, self.config['execution']['max_workers'])
        elif task_type == TaskType.IO_INTENSIVE:
            return min(cpu_count * 2, self.config['execution']['max_workers'] * 2)
        else:
            return min(cpu_count, self.config['execution']['max_workers'])

    def _run_in_executor_safe(self, executor, func):
        """Run coroutine func in executor safely - handles running loop correctly.

        M1-SAFE: When a loop is already running, use run_until_complete on the
        existing loop from the worker thread. This avoids creating a nested event
        loop with asyncio.run() which crashes Metal on Apple Silicon M1.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(func())
            finally:
                new_loop.close()
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(func())

    async def _execute_round_robin(self, tasks: list[Any], max_workers: int) -> list[Any]:
        """Execute tasks using round-robin distribution"""
        logger.info('Using round-robin execution strategy')
        chunk_size = max(1, len(tasks) // max_workers)
        task_chunks = [tasks[i:i + chunk_size] for i in range(0, len(tasks), chunk_size)]

        async def execute_chunk(chunk):
            results = []
            for task in chunk:
                result = await self._execute_with_semaphore(task)
                results.append(result)
            return results
        chunk_tasks = [execute_chunk(chunk) for chunk in task_chunks]
        chunk_results_raw = await safe_gather_ok(*chunk_tasks, label='execution_optimizer:489')
        chunk_results: list[list[Any]] = [r for r in chunk_results_raw if isinstance(r, list)]
        return [result for chunk_result in chunk_results for result in chunk_result]

    async def _execute_load_balanced(self, tasks: list[Any], max_workers: int) -> list[Any]:
        """Execute tasks with load balancing"""
        logger.info('Using load-balanced execution strategy')
        worker_loads = await self.load_balancer.get_worker_loads()
        task_distribution = self._distribute_tasks_load_balanced(tasks, worker_loads, max_workers)

        async def execute_worker_tasks(worker_id, worker_tasks):
            results = []
            for task in worker_tasks:
                try:
                    await self._execute_with_semaphore(task)
                except Exception as e:
                    logger.error(f'Task failed on worker {worker_id}: {e}')
                    results.append(None)
            return results
        worker_tasks = [execute_worker_tasks(worker_id, tasks) for worker_id, tasks in task_distribution.items()]
        worker_results_raw = await safe_gather_ok(*worker_tasks, label='execution_optimizer:521')
        worker_results: list[list[Any]] = [r for r in worker_results_raw if isinstance(r, list)]
        return [result for worker_result in worker_results for result in worker_result]

    async def _execute_resource_aware(self, tasks: list[Any], max_workers: int) -> list[Any]:
        """Execute tasks with resource awareness"""
        logger.info('Using resource-aware execution strategy')
        system_resources = await self.resource_monitor.get_current_resources()
        adjusted_workers = self._adjust_workers_for_resources(max_workers, system_resources)
        task_classifications = await self._classify_tasks_by_resources(tasks)
        workers: int = int(adjusted_workers) if adjusted_workers is not None else 1
        return await self._execute_with_resource_constraints(tasks, task_classifications, workers)

    async def _execute_predictive(self, tasks: list[Any], max_workers: int) -> list[Any]:
        """Execute tasks with predictive optimization"""
        logger.info('Using predictive execution strategy')
        if not self.task_history:
            logger.warning('No task history available for prediction, falling back to adaptive strategy')
            return await self._execute_adaptive(tasks, max_workers, TaskType.MIXED)
        await self._train_prediction_model()
        task_predictions = await self._predict_task_times(tasks)
        optimized_tasks = self._optimize_execution_order(tasks, task_predictions)
        return await self._execute_with_dynamic_workers(optimized_tasks, task_predictions, max_workers)

    async def _execute_adaptive(self, tasks: list[Any], max_workers: int, task_type: TaskType) -> list[Any]:
        """Execute tasks with adaptive strategy"""
        logger.info('Using adaptive execution strategy')
        initial_resources = await self.resource_monitor.get_current_resources()
        performance_samples = []
        current_workers: int = max(1, max_workers // 2)
        results = []
        task_index = 0
        while task_index < len(tasks):
            batch_size = min(current_workers * 2, len(tasks) - task_index)
            batch = tasks[task_index:task_index + batch_size]
            batch_start = time.time()
            batch_result = await parallel([self._execute_with_semaphore(task) for task in batch], taskgroup=True, policy='collect', ctx='batch_optimization', logger_instance=logger)
            for r in batch_result.ok:
                results.append(r)
            for exc in batch_result.errors:
                logger.warning('batch optimization task failed: %s: %s', type(exc).__name__, exc)
            if batch_result.re_raised is not None:
                raise batch_result.re_raised
            batch_time = time.time() - batch_start
            performance_samples.append({'workers': current_workers, 'time': batch_time, 'tasks': len(batch), 'throughput': len(batch) / batch_time})
            current_resources = await self.resource_monitor.get_current_resources()
            adapted = self._adapt_worker_count(current_workers, performance_samples, current_resources, initial_resources)
            current_workers = int(adapted) if adapted is not None else current_workers
            task_index += batch_size
        return results

    async def _execute_interpreter_pool(self, tasks: list[Any], max_workers: int) -> list[Any]:
        """Execute pure-Python CPU-bound batch via InterpreterPoolExecutor (P2-1).

        Uses Python 3.14 subinterpreters for true parallelism without GIL contention.
        Each subinterpreter has its own GIL → unlike ThreadPool, no GIL serialization.
        M1 8GB: ~1-2MB overhead per subinterpreter, max_workers capped at 2.

        Falls back to ThreadPoolExecutor if InterpreterPool unavailable.

        NOTE: This method expects tasks to be (data, func) tuples where func is a
        module-level callable that can be pickled for subinterpreter dispatch.
        Use execute_batch_interpreter() for the canonical batch(data, func) API.

        Args:
            tasks: List of (data, func) tuples from caller.
            max_workers: Max subinterpreters. Capped at 2 for M1 8GB safety.

        Returns:
            Flattened results from all subinterpreter workers.
        """
        effective_workers = min(max_workers, 2)
        if not tasks:
            return []
        first_task = tasks[0]
        if not (isinstance(first_task, tuple) and len(first_task) == 2):
            return await self._execute_round_robin(tasks, effective_workers)
        data, func = first_task
        try:
            from concurrent.futures import InterpreterPoolExecutor

            def _run_batch() -> list[Any]:
                with InterpreterPoolExecutor(max_workers=effective_workers) as exc:
                    chunk_size = max(1, len(data) // effective_workers)
                    futures = [exc.submit(func, data[i * chunk_size:(i + 1) * chunk_size]) for i in range(effective_workers)]
                    results: list[Any] = []
                    for f in futures:
                        results.extend(f.result())
                    return results
            return await asyncio.to_thread(_run_batch)
        except ImportError:
            logger.debug('InterpreterPoolExecutor not available (Python < 3.14)')
            return func(data)
        except Exception as exc:
            logger.warning('InterpreterPoolExecutor batch failed: %s — falling back to serial', exc)
            return func(data)

    def execute_batch_interpreter(self, data: list[Any], func: Callable[[list[Any]], list[Any]], max_workers: int | None=None) -> list[Any]:
        """Synchronous batch executor — call from async context via asyncio.to_thread().

        P2-1: Canonical API for InterpreterPoolExecutor batch execution.
        Chunks data and distributes to subinterpreter workers for true parallelism.

        Args:
            data: Input data (list of items to process).
            func: Pure-Python function (list -> list). Must be module-level
                  and pickle-able for subinterpreter dispatch.
            max_workers: Subinterpreters count. Default 2 (M1 8GB safe).

        Returns:
            Flattened results from all workers.

        Example:
            results = await asyncio.to_thread(
                optimizer.execute_batch_interpreter,
                items,
                normalize_text,
            )
        """
        effective_workers = min(max_workers or 2, 2)
        try:
            from concurrent.futures import InterpreterPoolExecutor
            with InterpreterPoolExecutor(max_workers=effective_workers) as exc:
                chunk_size = max(1, len(data) // effective_workers)
                futures = [exc.submit(func, data[i * chunk_size:(i + 1) * chunk_size]) for i in range(effective_workers)]
                results: list[Any] = []
                for f in futures:
                    results.extend(f.result())
                return results
        except ImportError:
            logger.debug('InterpreterPoolExecutor not available — running serial')
            return func(data)
        except Exception as exc:
            logger.warning('InterpreterPool batch failed: %s — running serial', exc)
            return func(data)

    async def _calculate_resource_allocation(self, tasks: list[Any], max_workers: int) -> dict[str, Any]:
        """Calculate optimal resource allocation for task group"""
        total_tasks = len(tasks)
        system_memory = psutil.virtual_memory().total / 1024 ** 3
        cpu_cores = multiprocessing.cpu_count()
        allocation: dict[str, Any] = {'cpu_cores_per_worker': cpu_cores / max_workers, 'memory_gb_per_worker': system_memory / max_workers * 0.8, 'expected_throughput': total_tasks / max_workers, 'estimated_completion_time': self._estimate_completion_time(tasks, max_workers)}
        return allocation

    def _distribute_tasks_load_balanced(self, tasks: list[Any], worker_loads: dict[str, float], max_workers: int) -> dict[str, list[Any]]:
        """Distribute tasks among workers based on current loads"""
        distribution = {f'worker_{i}': [] for i in range(max_workers)}
        sorted_workers = sorted(worker_loads.items(), key=lambda x: x[1])
        for i, task in enumerate(tasks):
            worker_id = sorted_workers[i % len(sorted_workers)][0]
            distribution[worker_id].append(task)
        return distribution

    async def _classify_tasks_by_resources(self, tasks: list[Any]) -> list[dict[str, Any]]:
        """Classify tasks by their resource requirements"""
        classifications = []
        for task in tasks:
            task_info = {'task': task, 'cpu_intensive': False, 'memory_intensive': False, 'io_intensive': False}
            if hasattr(task, '__name__') and any((keyword in str(task.__name__).lower() for keyword in ['compute', 'calculate', 'process'])):
                task_info['cpu_intensive'] = True
            if hasattr(task, '__name__') and any((keyword in str(task.__name__).lower() for keyword in ['load', 'store', 'cache'])):
                task_info['memory_intensive'] = True
            if not any([task_info['cpu_intensive'], task_info['memory_intensive']]):
                task_info['io_intensive'] = True
            classifications.append(task_info)
        return classifications

    async def _execute_with_resource_constraints(self, tasks: list[Any], classifications: list[dict[str, Any]], max_workers: int) -> list[Any]:
        """Execute tasks with resource constraints"""
        cpu_tasks = []
        memory_tasks = []
        io_tasks = []
        for task, classification in zip(tasks, classifications, strict=False):
            if classification['cpu_intensive']:
                cpu_tasks.append(task)
            elif classification['memory_intensive']:
                memory_tasks.append(task)
            else:
                io_tasks.append(task)
        results = []
        if cpu_tasks:
            cpu_workers = min(max_workers // 2, len(cpu_tasks))
            logger.info(f'Executing {len(cpu_tasks)} CPU tasks with {cpu_workers} workers')
            cpu_result = await parallel([self._execute_with_semaphore(task) for task in cpu_tasks], taskgroup=True, policy='collect', ctx='cpu_optimization', logger_instance=logger)
            for r in cpu_result.ok:
                results.append(r)
            for exc in cpu_result.errors:
                logger.warning('batch optimization task failed: %s: %s', type(exc).__name__, exc)
            if cpu_result.re_raised is not None:
                raise cpu_result.re_raised
        if memory_tasks:
            memory_workers = min(max_workers // 3, len(memory_tasks))
            logger.info(f'Executing {len(memory_tasks)} memory tasks with {memory_workers} workers')
            memory_result = await parallel([self._execute_with_semaphore(task) for task in memory_tasks], taskgroup=True, policy='collect', ctx='memory_optimization', logger_instance=logger)
            for r in memory_result.ok:
                results.append(r)
            for exc in memory_result.errors:
                logger.warning('batch optimization task failed: %s: %s', type(exc).__name__, exc)
            if memory_result.re_raised is not None:
                raise memory_result.re_raised
        if io_tasks:
            io_workers = max_workers
            logger.info(f'Executing {len(io_tasks)} I/O tasks with {io_workers} workers')
            io_result = await parallel([self._execute_with_semaphore(task) for task in io_tasks], taskgroup=True, policy='collect', ctx='io_optimization', logger_instance=logger)
            for r in io_result.ok:
                results.append(r)
            for exc in io_result.errors:
                logger.warning('batch optimization task failed: %s: %s', type(exc).__name__, exc)
            if io_result.re_raised is not None:
                raise io_result.re_raised
        return results

    async def _train_prediction_model(self):
        """Train prediction model on historical task data"""
        if len(self.task_history) < 10:
            return
        X = []
        y = []
        for metrics in list(self.task_history)[-100:]:
            features = [len(str(metrics.task_id)), metrics.cpu_usage, metrics.memory_usage]
            X.append(features)
            y.append(metrics.execution_time)
        if X:
            X = np.array(X)
            y = np.array(y)
            self.execution_predictor.fit(X, y)
            logger.info('Prediction model trained on historical data')

    async def _predict_task_times(self, tasks: list[Any]) -> list[float]:
        """Predict execution times for tasks"""
        if len(self.task_history) < 10:
            return [1.0] * len(tasks)
        predictions = []
        for task in tasks:
            features = [len(str(task)), 0.5, 0.5]
            try:
                prediction = self.execution_predictor.predict([features])[0]
                predictions.append(max(0.1, prediction))
            except Exception:
                predictions.append(1.0)
        return predictions

    def _optimize_execution_order(self, tasks: list[Any], predictions: list[float]) -> list[Any]:
        """Optimize task execution order based on predictions"""
        task_predictions = list(zip(tasks, predictions, strict=False))
        task_predictions.sort(key=lambda x: x[1])
        return [task for task, _ in task_predictions]

    async def _execute_with_dynamic_workers(self, tasks: list[Any], predictions: list[float], max_workers: int) -> list[Any]:
        """Execute tasks with dynamic worker allocation"""
        results = []
        task_index = 0
        while task_index < len(tasks):
            remaining_tasks = len(tasks) - task_index
            remaining_predictions = predictions[task_index:]
            estimated_total_time = sum(remaining_predictions)
            optimal_workers = min(max_workers, max(1, int(remaining_tasks / max(estimated_total_time / 60, 1))))
            batch_size = min(optimal_workers * 2, len(tasks) - task_index)
            batch = tasks[task_index:task_index + batch_size]
            batch_result = await parallel([self._execute_with_semaphore(task) for task in batch], taskgroup=True, policy='collect', ctx='predictive_optimization', logger_instance=logger)
            for r in batch_result.ok:
                results.append(r)
            for exc in batch_result.errors:
                logger.warning('batch optimization task failed: %s: %s', type(exc).__name__, exc)
            if batch_result.re_raised is not None:
                raise batch_result.re_raised
            task_index += batch_size
        return results

    def _adjust_workers_for_resources(self, max_workers: int, resources: dict[str, float]) -> int | None:
        """Adjust worker count based on available resources"""
        cpu_threshnew = self.config['threshnews']['cpu_threshnew']
        memory_threshnew = self.config['threshnews']['memory_threshnew']
        if resources['cpu_usage'] > cpu_threshnew:
            max_workers = max(1, int(max_workers * (1 - resources['cpu_usage'])))
        if resources['memory_usage'] > memory_threshnew:
            max_workers = max(1, int(max_workers * (1 - resources['memory_usage'])))
            return max_workers

    def _adapt_worker_count(self, current_workers: int, performance_samples: list[dict[str, float]], current_resources: dict[str, float], initial_resources: dict[str, float]) -> int | None:
        """Adapt worker count based on performance and resources"""
        if len(performance_samples) < 2:
            return current_workers
        cpu_val: Any = current_resources.get('cpu_usage', 0.0)
        mem_val: Any = current_resources.get('memory_usage', 0.0)
        cpu_usage: float = float(cpu_val) if cpu_val is not None else 0.0
        memory_usage: float = float(mem_val) if mem_val is not None else 0.0
        recent_throughput = performance_samples[-1]['throughput']
        previous_throughput = performance_samples[-2]['throughput']
        throughput_change = (recent_throughput - previous_throughput) / previous_throughput
        cpu_threshnew = self.config['threshnews']['cpu_threshnew']
        memory_threshnew = self.config['threshnews']['memory_threshnew']
        new_workers = current_workers
        if throughput_change > 0.1 and cpu_usage < cpu_threshnew and (memory_usage < memory_threshnew):
            new_workers = min(current_workers + 1, self.config['execution']['max_workers'])
        elif throughput_change < -0.1 or cpu_usage > cpu_threshnew or memory_usage > memory_threshnew:
            new_workers = max(1, current_workers - 1)
        if new_workers != current_workers:
            logger.info(f'Adapting worker count: {current_workers} -> {new_workers}')
            return new_workers

    def _estimate_completion_time(self, tasks: list[Any], max_workers: int) -> float | None:
        """Estimate completion time for task group"""
        if not tasks:
            return 0.0
        if self.task_history:
            avg_task_time = np.mean([m.execution_time for m in list(self.task_history)[-20:]])
            estimated_time = len(tasks) / max_workers * avg_task_time
        else:
            estimated_time = len(tasks) * 0.1
            return estimated_time

    async def _record_execution_metrics(self, group_id: str, execution_time: float, task_count: int):
        """Record execution metrics for group"""
        if group_id in self.parallel_groups:
            stored = self.parallel_groups[group_id]
            group = stored.get('payload', stored)
            if isinstance(group, dict):
                _start_time = stored.get('ts', datetime.now(UTC))
            else:
                _start_time = group.created_at
            # E3 FIX: use cached system_snapshot instead of raw psutil calls
            # cpu_percent removed — was non-blocking but still ~µs overhead per call
            # memory from mach host_statistics64 via get_system_snapshot (cached, zero-syscall warm)
            try:
                from hledac.universal.core.system_metrics import get_system_snapshot
                snap = get_system_snapshot()
                memory_usage = snap.memory_percent / 100.0
            except Exception:
                memory_usage = 0.0
            metrics = TaskMetrics(task_id=group_id, task_type=TaskType.MIXED, start_time=_start_time, end_time=datetime.now(UTC), cpu_usage=0.0, memory_usage=memory_usage, execution_time=execution_time, success=True, parallel_group=group_id)
            self.task_history.append(metrics)

    def get_performance_statistics(self) -> dict[str, Any]:
        """Get performance statistics"""
        if not self.task_history:
            return {}
        recent_metrics = list(self.task_history)[-50:]
        stats = {'total_executions': len(self.task_history), 'average_execution_time': np.mean([m.execution_time for m in recent_metrics]), 'average_cpu_usage': np.mean([m.cpu_usage for m in recent_metrics]), 'average_memory_usage': np.mean([m.memory_usage for m in recent_metrics]), 'success_rate': np.mean([m.success for m in recent_metrics]), 'total_parallel_groups': len(self.parallel_groups), 'active_workers': len(self.worker_metrics)}
        return stats

    def get_bounded_ops_telemetry(self) -> dict[str, Any]:
        """Return telemetry for bounded pending ops.

        F214OPT-D: Exposes pending ops limits and throttling metrics.
        """
        return {'execution_max_pending': self._execution_max_pending, 'execution_pending_throttled_count': self._execution_pending_throttled_count}

    def export_performance_report(self, filepath: str):
        """Export detailed performance report"""
        report = {'timestamp': datetime.now(UTC).isoformat(), 'statistics': self.get_performance_statistics(), 'parallel_groups': {group_id: {'strategy': group.strategy.value, 'max_workers': group.max_workers, 'task_count': len(group.tasks), 'resource_allocation': group.resource_allocation, 'created_at': group.created_at.isoformat()} for group_id, group in self.parallel_groups.items()}, 'recent_executions': [{'task_id': metrics.task_id, 'task_type': metrics.task_type.value, 'execution_time': metrics.execution_time, 'cpu_usage': metrics.cpu_usage, 'memory_usage': metrics.memory_usage, 'success': metrics.success, 'parallel_group': metrics.parallel_group} for metrics in list(self.task_history)[-20:]]}
        with open(filepath, 'w') as f:
            f.write(_json.encode(report, indent=2).decode('utf-8'))
        logger.info(f'Performance report exported to {filepath}')

    async def cleanup(self):
        """Clean up resources.

        Note: thread_pool is a shared domain_executor (ISSUE-049: get_parallel_executor).
        Do NOT call shutdown() — shared executor lifetime is managed by
        atexit.register(shutdown_all) in domain_executors.
        """
        await self._concurrency_controller.stop_monitoring()
        self.thread_pool = None  # type: ignore[assignment]
        logger.info('Parallel execution optimizer cleaned up')

class LoadBalancer:
    """Load balancer for task distribution"""
    __slots__ = tuple(('worker_loads',))

    def __init__(self):
        self.worker_loads = {}

    async def get_worker_loads(self) -> dict[str, float]:
        """Get current worker loads"""
        return self.worker_loads

    def update_worker_load(self, worker_id: str, load: float):
        """Update worker load"""
        self.worker_loads[worker_id] = load

class ResourceMonitor:
    """Resource monitoring for optimization"""

    async def get_current_resources(self) -> dict[str, float]:
        """Get current system resources.

        E3 FIX: uses get_system_snapshot() — mach host_statistics64 cached,
        no raw psutil syscalls in this hot path.
        """
        try:
            from hledac.universal.core.system_metrics import get_system_snapshot
            snap = get_system_snapshot()
            return {
                'cpu_usage': 0.0,  # Not available from mach without blocking call
                'memory_usage': snap.memory_percent / 100.0,
                'available_memory_gb': snap.memory_available_gb,
                'cpu_count': multiprocessing.cpu_count(),
            }
        except Exception:
            return {'cpu_usage': 0.0, 'memory_usage': 0.0, 'available_memory_gb': 0.0, 'cpu_count': multiprocessing.cpu_count()}

class ResourceType(Enum):
    """Types of system resources."""
    CPU = 'cpu'
    MEMORY = 'memory'
    GPU = 'gpu'
    DISK = 'disk'
    NETWORK = 'network'

class OptimizationLevel(Enum):
    """Optimization aggressiveness levels."""
    CONSERVATIVE = 'conservative'
    BALANCED = 'balanced'
    AGGRESSIVE = 'aggressive'

class ResourceMetrics(msgspec.Struct):
    """Current resource utilization metrics."""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_available_gb: float = 0.0
    gpu_utilization: float | None = None
    disk_usage_percent: float = 0.0
    network_bytes_sent: int = 0
    network_bytes_recv: int = 0
    timestamp: float = field(default_factory=time.time)

class ResourceLimits(msgspec.Struct):
    """Resource utilization limits for M1 8GB systems."""
    max_cpu_percent: float = 80.0
    max_memory_percent: float = 85.0
    max_memory_gb: float = 6.0
    emergency_memory_gb: float = 5.5
    max_disk_percent: float = 90.0

class AnomalyDetector:
    """
    Anomaly detection for resource monitoring.

    Detects resource usage spikes using statistical analysis
    (Z-score based detection with configurable thresholds).
    """
    __slots__ = tuple(('threshold',))

    def __init__(self, threshold: float=2.0):
        self.threshold = threshold

    def detect_anomalies(self, metrics_history: list[ResourceMetrics]) -> list[str]:
        """Detect anomalies in resource metrics."""
        if len(metrics_history) < 5:
            return []
        anomalies = []
        memory_values = [m.memory_percent for m in metrics_history[-10:]]
        if self._is_anomaly(memory_values):
            anomalies.append('memory_usage_spike')
        cpu_values = [m.cpu_percent for m in metrics_history[-10:]]
        if self._is_anomaly(cpu_values):
            anomalies.append('cpu_usage_spike')
        return anomalies

    def _is_anomaly(self, values: list[float]) -> bool:
        """Check if latest value is anomalous using Z-score."""
        if len(values) < 3:
            return False
        import statistics
        mean = sum(values[:-1]) / len(values[:-1])
        std_dev = statistics.stdev(values[:-1]) if len(values) > 2 else 0
        latest = values[-1]
        if std_dev == 0:
            return abs(latest - mean) > 10
        z_score = abs(latest - mean) / std_dev
        return z_score > self.threshold

class PredictiveScaler:
    """
    Predictive scaling based on workload patterns.

    Analyzes resource usage trends to predict scaling needs
    and provide recommendations for workload optimization.
    """

    def predict_scaling_needs(self, metrics_history: list[ResourceMetrics], task_requirements: dict[str, Any]) -> dict[str, Any]:
        """Predict scaling needs based on historical data."""
        if len(metrics_history) < 5:
            return {'recommendation': 'maintain_current', 'confidence': 0.5}
        recent_memory = [m.memory_percent for m in metrics_history[-5:]]
        memory_trend = recent_memory[-1] - recent_memory[0]
        if memory_trend > 10:
            return {'recommendation': 'scale_down', 'confidence': 0.8}
        elif memory_trend < -10:
            return {'recommendation': 'scale_up', 'confidence': 0.7}
        return {'recommendation': 'maintain_current', 'confidence': 0.6}

    def analyze_workload_pattern(self, metrics_history: list[ResourceMetrics]) -> dict[str, Any]:
        """Analyze workload patterns for optimization recommendations."""
        if len(metrics_history) < 3:
            return {'pattern': 'insufficient_data', 'confidence': 0.0}
        cpu_values = [m.cpu_percent for m in metrics_history]
        memory_values = [m.memory_percent for m in metrics_history]
        cpu_trend = cpu_values[-1] - cpu_values[0]
        memory_trend = memory_values[-1] - memory_values[0]
        if cpu_trend > 20 and memory_trend > 20:
            pattern = 'resource_intensive_increasing'
        elif cpu_trend < -20 and memory_trend < -20:
            pattern = 'resource_intensive_decreasing'
        elif abs(cpu_trend) < 10 and abs(memory_trend) < 10:
            pattern = 'stable'
        else:
            pattern = 'mixed'
        return {'pattern': pattern, 'cpu_trend': cpu_trend, 'memory_trend': memory_trend, 'confidence': 0.7}

class IntelligentResourceAllocator:
    """
    Intelligent Resource Allocator - M1-Optimized Resource Management

    Dynamically allocates tasks to Performance (P) or Efficiency (E) cores
    based on workload characteristics and system state.

    M1-Specific Features:
    - P-core detection: hw.perflevel0.logicalcpu (cores 1-3 on M1 Air)
    - E-core detection: hw.perflevel1.logicalcpu (core 0 on M1 Air)
    - Dynamic workload balancing between core types
    - Thermal-aware throttling
    """
    __slots__ = tuple(('allocation_history', 'e_cores', 'is_apple_silicon', 'p_cores', 'thermal_state'))

    def __init__(self):
        self.p_cores: list[int] = []
        self.e_cores: list[int] = []
        self.is_apple_silicon: bool = False
        self._detect_m1_cores()
        self.allocation_history: deque = deque(maxlen=100)
        self.thermal_state: str = 'normal'
        logger.info(f'IntelligentResourceAllocator: P-cores={self.p_cores}, E-cores={self.e_cores}')

    def _detect_m1_cores(self) -> None:
        """Detect M1 P/E core topology using sysctl"""
        import platform
        import subprocess
        if platform.system() != 'Darwin':
            logger.info('Not macOS - using generic CPU topology')
            self._fallback_to_generic_topology()
            return
        try:
            result = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True, timeout=5)
            cpu_brand = result.stdout.strip()
            if 'Apple' in cpu_brand:
                self.is_apple_silicon = True
                logger.info(f'Detected Apple Silicon: {cpu_brand}')
                p_cores_result = subprocess.run(['sysctl', '-n', 'hw.perflevel0.logicalcpu'], capture_output=True, text=True, timeout=5)
                p_core_count = int(p_cores_result.stdout.strip())
                e_cores_result = subprocess.run(['sysctl', '-n', 'hw.perflevel1.logicalcpu'], capture_output=True, text=True, timeout=5)
                e_core_count = int(e_cores_result.stdout.strip())
                total_cores = p_core_count + e_core_count
                self.e_cores = list(range(e_core_count))
                self.p_cores = list(range(e_core_count, total_cores))
                logger.info(f'M1 Core Topology: {p_core_count} P-cores, {e_core_count} E-cores')
            else:
                logger.info(f'Non-Apple CPU: {cpu_brand}')
                self._fallback_to_generic_topology()
        except Exception as e:
            logger.warning(f'Failed to detect M1 cores: {e}')
            self._fallback_to_generic_topology()

    def _fallback_to_generic_topology(self) -> None:
        """Fallback to generic CPU topology detection"""
        import os
        cpu_count = os.cpu_count() or 4
        mid = cpu_count // 2
        self.e_cores = list(range(mid))
        self.p_cores = list(range(mid, cpu_count))
        logger.info(f'Generic topology: {len(self.p_cores)} performance threads, {len(self.e_cores)} efficiency threads')

    def allocate_task(self, task_priority: str='normal', cpu_intensity: float=0.5) -> dict[str, Any]:
        """
        Allocate a task to appropriate core type

        Args:
            task_priority: "low", "normal", "high", "critical"
            cpu_intensity: 0.0-1.0 scale of CPU intensity

        Returns:
            Allocation configuration with CPU affinity
        """
        allocation: dict[str, Any] = {'core_type': 'any', 'cpu_affinity': None, 'priority_boost': False, 'thermal_throttle': False}
        if self.thermal_state == 'critical':
            allocation['thermal_throttle'] = True
            if self.e_cores:
                allocation['core_type'] = 'efficiency'
                allocation['cpu_affinity'] = self.e_cores
            return allocation
        if task_priority in ['high', 'critical'] or cpu_intensity > 0.7:
            if self.p_cores and (not self._are_p_cores_overloaded()):
                allocation['core_type'] = 'performance'
                allocation['cpu_affinity'] = self.p_cores
                allocation['priority_boost'] = task_priority == 'critical'
            elif self.e_cores:
                allocation['core_type'] = 'efficiency'
                allocation['cpu_affinity'] = self.e_cores
        elif task_priority == 'low' or cpu_intensity < 0.3:
            if self.e_cores:
                allocation['core_type'] = 'efficiency'
                allocation['cpu_affinity'] = self.e_cores
            elif self.p_cores:
                allocation['core_type'] = 'performance'
                allocation['cpu_affinity'] = self.p_cores
        else:
            allocation['core_type'] = 'balanced'
            all_cores = self.e_cores + self.p_cores
            if all_cores:
                allocation['cpu_affinity'] = all_cores
        self.allocation_history.append({'timestamp': datetime.now(UTC), 'priority': task_priority, 'cpu_intensity': cpu_intensity, 'allocation': allocation.copy()})
        return allocation

    def _are_p_cores_overloaded(self) -> bool:
        """Check if P-cores are overloaded based on recent allocations"""
        if not self.p_cores:
            return True
        recent_p_allocations = sum((1 for alloc in self.allocation_history if alloc['allocation']['core_type'] == 'performance'))
        return recent_p_allocations > len(self.allocation_history) * 0.7

    def get_optimal_thread_count(self, task_type: str='mixed') -> int:
        """
        Get optimal thread count based on task type and core topology

        Args:
            task_type: "cpu_bound", "io_bound", "mixed"

        Returns:
            Recommended thread count
        """
        total_cores = len(self.p_cores) + len(self.e_cores)
        if task_type == 'cpu_bound':
            return max(1, len(self.p_cores))
        elif task_type == 'io_bound':
            return max(2, total_cores * 2)
        else:
            return max(2, total_cores)

    def get_core_statistics(self) -> dict[str, Any]:
        """Get core allocation statistics"""
        return {'p_cores': self.p_cores, 'e_cores': self.e_cores, 'is_apple_silicon': self.is_apple_silicon, 'thermal_state': self.thermal_state, 'recent_allocations': len(self.allocation_history), 'p_core_allocation_ratio': self._calculate_p_core_ratio()}

    def _calculate_p_core_ratio(self) -> float:
        """Calculate ratio of P-core to total allocations"""
        if not self.allocation_history:
            return 0.5
        p_allocations = sum((1 for alloc in self.allocation_history if alloc['allocation']['core_type'] == 'performance'))
        return p_allocations / len(self.allocation_history)

    def apply_thermal_throttling(self, state: str) -> None:
        """
        Apply thermal throttling state

        Args:
            state: "normal", "elevated", "critical"
        """
        self.thermal_state = state
        logger.warning(f'Thermal state changed to: {state}')
        if state == 'critical':
            logger.warning('Critical thermal state - forcing E-core only allocation')

def create_m1_resource_allocator() -> IntelligentResourceAllocator:
    """Factory function to create M1-optimized resource allocator"""
    return IntelligentResourceAllocator()

async def main():
    """Main function for parallel execution optimizer testing"""
    optimizer = ParallelExecutionOptimizer()

    async def example_task(task_id):
        await asyncio.sleep(0.1 + task_id % 3 * 0.05)
        return f'Task {task_id} completed'
    tasks = [lambda i=i: example_task(i) for i in range(20)]
    strategies = [ExecutionStrategy.ROUND_ROBIN, ExecutionStrategy.LOAD_BALANCED, ExecutionStrategy.ADAPTIVE]
    for strategy in strategies:
        print(f'\nTesting {strategy.value} strategy:')
        start_time = time.time()
        results = await optimizer.execute_parallel(tasks[:10], strategy=strategy)
        execution_time = time.time() - start_time
        print(f'  Execution time: {execution_time:.2f} seconds')
        print(f'  Results: {len(results)} tasks completed')
    optimizer.export_performance_report('parallel_execution_report.json')
    await optimizer.cleanup()
if __name__ == '__main__':
    asyncio.run(main())

class CacheEntry(msgspec.Struct):
    """Entry in predictive cache."""
    key: str
    value: Any
    access_count: int = 0
    last_access_time: float = field(default_factory=time.time)
    predicted_next_access: float = 0.0
    size_bytes: int = 0

class PredictiveCacheManager:
    """
    Advanced caching with predictive eviction.

    Uses access pattern analysis to predict future accesses
    and evict items that won't be needed soon.
    """
    __slots__ = tuple(('_current_size', '_lock', 'access_history', 'access_patterns', 'cache', 'max_entries', 'max_size_bytes'))

    def __init__(self, max_size_bytes: int=100 * 1024 * 1024, max_entries: int=1000):
        self.max_size_bytes = max_size_bytes
        self.max_entries = max_entries
        self.cache: dict[str, CacheEntry] = {}
        self.access_history: deque = deque(maxlen=1000)
        self._current_size = 0
        self._lock = threading.RLock()
        self.access_patterns: dict[str, list[float]] = defaultdict(list)

    def get(self, key: str) -> Any | None:
        """Get value from cache with access tracking."""
        with self._lock:
            if key not in self.cache:
                return None
            entry = self.cache[key]
            current_time = time.time()
            entry.access_count += 1
            entry.last_access_time = current_time
            self.access_history.append({'key': key, 'time': current_time})
            self.access_patterns[key].append(current_time)
            if len(self.access_patterns[key]) > 100:
                self.access_patterns[key] = self.access_patterns[key][-100:]
            return entry.value

    def put(self, key: str, value: Any, size_bytes: int | None=None):
        """Put value into cache with predictive eviction."""
        if size_bytes is None:
            size_bytes = len(str(value).encode())
        with self._lock:
            while self._current_size + size_bytes > self.max_size_bytes or len(self.cache) >= self.max_entries:
                if not self._evict_one():
                    break
            if key in self.cache:
                old_entry = self.cache[key]
                self._current_size -= old_entry.size_bytes
            entry = CacheEntry(key=key, value=value, size_bytes=size_bytes)
            entry.predicted_next_access = self._predict_next_access(key)
            self.cache[key] = entry
            self._current_size += size_bytes
            return True

    def _evict_one(self) -> bool:
        """Evict one item using predictive strategy."""
        if not self.cache:
            return False
        current_time = time.time()
        eviction_scores = []
        for key, entry in self.cache.items():
            time_since_access = current_time - entry.last_access_time
            predicted_wait = entry.predicted_next_access - current_time
            score = time_since_access + predicted_wait - entry.access_count * 10
            eviction_scores.append((key, score))
        eviction_scores.sort(key=lambda x: x[1], reverse=True)
        evict_key = eviction_scores[0][0]
        entry = self.cache.pop(evict_key)
        self._current_size -= entry.size_bytes
        return True

    def _predict_next_access(self, key: str) -> float:
        """Predict when key will be accessed next."""
        if key not in self.access_patterns or len(self.access_patterns[key]) < 2:
            return time.time() + 3600
        accesses = self.access_patterns[key]
        intervals = [accesses[i] - accesses[i - 1] for i in range(1, len(accesses))]
        avg_interval = sum(intervals) / len(intervals)
        last_access = accesses[-1]
        predicted_next = last_access + avg_interval
        return predicted_next

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            hit_rate = 0.0
            if self.access_history:
                recent_accesses = list(self.access_history)[-100:]
                hits = sum((1 for a in recent_accesses if a['key'] in self.cache))
                hit_rate = hits / len(recent_accesses) if recent_accesses else 0
            return {'entries': len(self.cache), 'size_bytes': self._current_size, 'max_size_bytes': self.max_size_bytes, 'hit_rate': hit_rate, 'patterns_tracked': len(self.access_patterns)}

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self.cache.clear()
            self._current_size = 0
            self.access_history.clear()

class MemoryAwareScheduler:
    """
    Task scheduler that respects memory constraints.
    Prevents OOM by controlling concurrent task execution.
    """
    __slots__ = tuple(('_semaphore', 'active_tasks', 'max_memory_percent'))

    def __init__(self, max_memory_percent: float=80.0):
        self.max_memory_percent = max_memory_percent
        self.active_tasks: dict[str, dict[str, Any]] = {}
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        self._semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)

    async def schedule(self, task_id: str, task_func: Callable, estimated_memory_mb: float=100):
        """Schedule task with memory awareness.

        E3 FIX: uses cached get_system_snapshot() instead of raw psutil.virtual_memory()
        which was called on every scheduled task (hot path).
        """
        try:
            from hledac.universal.core.system_metrics import get_system_snapshot
            snap = get_system_snapshot()
            if snap.memory_percent > self.max_memory_percent:
                logger.warning(f'Memory high ({snap.memory_percent:.1f}%), throttling task {task_id}')
                await asyncio.sleep(1)
        except Exception:
            pass  # fail-safe: proceed without blocking
        async with self._semaphore:
            self.active_tasks[task_id] = {'start_time': time.time(), 'estimated_memory': estimated_memory_mb}
            try:
                result = await task_func() if inspect.iscoroutinefunction(task_func) else task_func()
                return result
            finally:
                del self.active_tasks[task_id]

    def get_active_count(self) -> int:
        """Get number of active tasks."""
        return len(self.active_tasks)

def auto_optimize(cache_results: bool=True, max_workers: int | None=None, memory_limit_mb: float=512.0):
    """
    Decorator for automatic function optimization.

    Args:
        cache_results: Whether to cache function results
        max_workers: Max parallel workers (None = auto)
        memory_limit_mb: Memory limit for execution
    """

    def decorator(func: Callable) -> Callable:
        cache_manager: Any = PredictiveCacheManager() if cache_results else None
        func_name: str = getattr(func, '__name__', repr(func))

        async def wrapper(*args, **kwargs):
            cache_key: str = ''
            if cache_manager:
                cache_key = f'{func_name}:{hash(str(args))}:{hash(str(kwargs))}'
                cached = cache_manager.get(cache_key)
                if cached is not None:
                    return cached
            start_time = time.time()
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            if cache_manager:
                execution_time = time.time() - start_time
                if execution_time > 0.1:
                    cache_manager.put(cache_key, result)
            return result
        wrapper._cache_manager = cache_manager
        return wrapper
    return decorator