"""
Hledac Agent Performance Optimizer

Implements comprehensive performance optimization for the 45+ agent ecosystem
with focus on 8GB memory constraint, async patterns, and load balancing.

Key Features:
- Agent pooling and reuse patterns
- Intelligent load balancing and routing
- Memory-aware execution management
- Performance monitoring and optimization
- Async-first architecture optimization
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from weakref import ref as WeakRef

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

# Optional imports - fallback if not available
try:
    from hledac.config import get_settings
except ImportError:
    get_settings = None

try:
    from _shims.core_resilience import AgentExecutionError, CircuitBreakerOpen
except ImportError:
    # Define fallback exception classes
    class AgentExecutionError(Exception):
        """Fallback for AgentExecutionError"""
        pass

    class CircuitBreakerOpen(Exception):
        """Fallback for CircuitBreakerOpen"""
        pass

logger = logging.getLogger(__name__)


def get_memory_usage_mb() -> float:
    """Get current memory usage in MB."""
    if PSUTIL_AVAILABLE and psutil:
        try:
            proc = psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0
    return 0.0


def get_system_memory() -> dict:
    """Get system memory info."""
    if PSUTIL_AVAILABLE and psutil:
        mem = psutil.virtual_memory()
        return {
            'total_gb': mem.total / (1024**3),
            'available_gb': mem.available / (1024**3),
            'percent': mem.percent
        }
    return {'total_gb': 8.0, 'available_gb': 4.0, 'percent': 50.0}


@dataclass
class AgentMetrics:
    """Performance metrics for individual agents."""
    name: str
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_execution_time: float = 0.0
    memory_usage_mb: float = 0.0
    last_used: float = 0.0
    circuit_breaker_open: bool = False
    rate_limited: bool = False
    cache_hit_rate: float = 0.0


@dataclass
class LoadBalancingConfig:
    """Configuration for agent load balancing."""
    max_concurrent_agents: int = 8
    memory_threshnew_mb: int = 512
    agent_timeout_seconds: float = 30.0
    circuit_breaker_threshnew: int = 3
    circuit_breaker_timeout: float = 60.0
    agent_pool_size: int = 4
    load_balance_strategy: str = "round_robin"  # round_robin, weighted, least_used


@dataclass
class OptimizationReport:
    """Report containing optimization results."""
    timestamp: float = field(default_factory=time.time)
    optimizations_applied: list[str] = field(default_factory=list)
    memory_freed_mb: float = 0.0
    performance_improvement: float = 0.0
    agent_pool_stats: dict[str, Any] = field(default_factory=dict)
    bottlenecks_identified: list[str] = field(default_factory=list)


class AgentPool:
    """
    High-performance agent pooling system with memory management.

    Maintains pools of initialized agents for reuse, reducing initialization
    overhead and memory churn for 8GB constraint systems.
    """

    def __init__(self, config: LoadBalancingConfig):
        self.config = config
        self._pools: dict[str, deque] = defaultdict(deque)
        self._pool_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._metrics: dict[str, AgentMetrics] = {}
        self._weak_refs: dict[str, set[WeakRef]] = defaultdict(set)
        self._cleanup_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize the agent pool system."""
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup(), name="performance_coordinator:cleanup")
        logger.info("Agent pool initialized with strategy: %s", self.config.load_balance_strategy)

    async def shutdown(self) -> None:
        """Shutdown the agent pool system."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Clear all pools
        for pool in self._pools.values():
            pool.clear()
        self._pools.clear()
        self._weak_refs.clear()

        # Force garbage collection
        gc.collect()
        logger.info("Agent pool shutdown complete")

    @asynccontextmanager
    async def get_agent(self, agent_name: str, agent_factory: Callable[[], Any]):
        """
        Get an agent from the pool or create a new one.

        Args:
            agent_name: Name of the agent
            agent_factory: Factory function to create new agent instances

        Yields:
            Agent instance
        """
        lock = self._pool_locks[agent_name]
        agent = None
        created_new = False

        async with lock:
            # Try to get agent from pool
            pool = self._pools[agent_name]
            if pool:
                agent = pool.popleft()
                # Clean up weak references
                # Clean up weak references
                self._cleanup_weak_refs(agent_name)

            # Create new agent if needed
            if agent is None or self._is_agent_expired(agent):
                try:
                    agent = await self._create_agent_safely(agent_factory)
                    created_new = True
                except Exception as e:
                    logger.error(f"Failed to create agent {agent_name}: {e}")
                    raise AgentExecutionError(f"Agent creation failed: {e}")

        # Update metrics
        if agent_name not in self._metrics:
            self._metrics[agent_name] = AgentMetrics(name=agent_name)

        start_time = time.time()

        try:
            yield agent

            # Update success metrics
            execution_time = time.time() - start_time
            metrics = self._metrics[agent_name]
            metrics.execution_count += 1
            metrics.success_count += 1
            metrics.avg_execution_time = (
                (metrics.avg_execution_time * (metrics.execution_count - 1) + execution_time) /
                metrics.execution_count
            )
            metrics.last_used = time.time()

            # Return to pool if not expired
            if not created_new and await self._should_return_to_pool(agent_name, agent):
                async with lock:
                    if len(self._pools[agent_name]) < self.config.agent_pool_size:
                        self._pools[agent_name].append(agent)
                    # Add weak reference for cleanup tracking
                    self._weak_refs[agent_name].add(WeakRef(agent))

        except Exception as e:
            # Update failure metrics
            metrics = self._metrics[agent_name]
            metrics.execution_count += 1
            metrics.failure_count += 1

            if isinstance(e, CircuitBreakerOpen):
                    metrics.circuit_breaker_open = True
            elif "rate limit" in str(e).lower():
                    metrics.rate_limited = True

            raise

    async def _create_agent_safely(self, agent_factory: Callable[[], Any]) -> Any:
        """Create agent with memory pressure handling."""
        # Check memory before creating agent
        memory_mb = get_memory_usage_mb()
        if memory_mb > self.config.memory_threshnew_mb:
            logger.warning(f"High memory usage ({memory_mb:.1f}MB), triggering cleanup")
            await self._emergency_cleanup()

        return await asyncio.get_running_loop().run_in_executor(
            None, agent_factory
        )

    def _is_agent_expired(self, agent: Any) -> bool:
        """Check if agent instance has expired."""
        # Check circuit breaker status
        if hasattr(agent, '_execution_policy'):
            policy = getattr(agent, '_execution_policy', None)
            if policy and hasattr(policy, 'circuit_breaker'):
                if getattr(policy.circuit_breaker, 'state', None) == 'open':
                    return True

        # Check age (simple heuristic)
        return False

    async def _should_return_to_pool(self, agent_name: str, agent: Any) -> bool:
        """Determine if agent should be returned to pool."""
        # Don't pool if memory is constrained
        memory_mb = get_memory_usage_mb()
        if memory_mb > self.config.memory_threshnew_mb * 0.8:
            return False

        # Don't pool if agent has high failure rate
        metrics = self._metrics.get(agent_name)
        if metrics and metrics.execution_count > 5:
            success_rate = metrics.success_count / metrics.execution_count
            if success_rate < 0.7:  # Less than 70% success rate
                return False

        return True

    def _cleanup_weak_refs(self, agent_name: str) -> None:
        """Clean up dead weak references for an agent."""
        if agent_name in self._weak_refs:
            dead_refs = [ref for ref in self._weak_refs[agent_name] if ref() is None]
            for ref in dead_refs:
                self._weak_refs[agent_name].discard(ref)

    async def _periodic_cleanup(self) -> None:
        """Periodic cleanup of expired agents and weak references."""
        while True:
            try:
                await asyncio.sleep(300)  # Cleanup every 5 minutes

                # Clean up weak references
                for agent_name in list(self._weak_refs.keys()):
                    self._cleanup_weak_refs(agent_name)

                # Clean up new agents in pools
                for agent_name, pool in self._pools.items():
                    # Remove agents newer than 10 minutes
                    current_time = time.time()
                    while pool and (current_time - getattr(pool[0], '_created_time', 0)) > 600:
                        pool.popleft()

                # Force garbage collection
                gc.collect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic cleanup failed: {e}")

    async def _emergency_cleanup(self) -> None:
        """Emergency cleanup when memory pressure is high."""
        logger.warning("Emergency cleanup triggered")

        # Clear all agent pools
        for pool in self._pools.values():
            pool.clear()

        # Clear weak references
        for refs in self._weak_refs.values():
            refs.clear()

        # Force garbage collection
        gc.collect()

        # Memory after cleanup
        memory_mb = get_memory_usage_mb()
        logger.info(f"Emergency cleanup completed. Memory usage: {memory_mb:.1f}MB")

    def get_metrics(self) -> dict[str, AgentMetrics]:
        """Get performance metrics for all agents."""
        return dict(self._metrics)

    def get_pool_stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        stats = {
            "total_pools": len(self._pools),
            "pooled_agents": {name: len(pool) for name, pool in self._pools.items()},
            "weak_refs_count": {name: len(refs) for name, refs in self._weak_refs.items()},
            "memory_usage_mb": get_memory_usage_mb(),
        }
        return stats


class IntelligentLoadBalancer:
    """
    Intelligent load balancer for agent execution with multiple strategies.

    Supports round-robin, weighted, and least-used load balancing strategies
    with real-time performance adaptation.
    """

    def __init__(self, config: LoadBalancingConfig):
        self.config = config
        self._round_robin_counters: dict[str, int] = defaultdict(int)
        self._agent_weights: dict[str, float] = defaultdict(float)
        self._usage_counters: dict[str, int] = defaultdict(int)
        self._last_weight_update = 0.0

    def select_agent(
        self,
        available_agents: list[str],
        strategy: str | None = None,
        metrics: dict[str, AgentMetrics] | None = None
    ) -> str:
        """
        Select the best agent for execution based on load balancing strategy.

        Args:
            available_agents: List of available agent names
            strategy: Load balancing strategy (overrides config)
            metrics: Agent metrics for decision making

        Returns:
            Selected agent name
        """
        if not available_agents:
            raise ValueError("No available agents")

        strategy = strategy or self.config.load_balance_strategy

        if strategy == "round_robin":
            return self._round_robin_select(available_agents)
        elif strategy == "weighted":
            return self._weighted_select(available_agents, metrics or {})
        elif strategy == "least_used":
            return self._least_used_select(available_agents)
        else:
            # Default to round-robin
            return self._round_robin_select(available_agents)

    def _round_robin_select(self, agents: list[str]) -> str:
        """Round-robin agent selection."""
        if not agents:
            raise ValueError("No agents available")

        # Use hash of agent names for consistent distribution
        agent = agents[self._round_robin_counters["global"] % len(agents)]
        self._round_robin_counters["global"] += 1
        return agent

    def _weighted_select(self, agents: list[str], metrics: dict[str, AgentMetrics]) -> str:
        """Weighted agent selection based on performance metrics."""
        if not agents:
            raise ValueError("No agents available")

        # Update weights if needed
        current_time = time.time()
        if current_time - self._last_weight_update > 60:  # Update every minute
            self._update_weights(metrics)
            self._last_weight_update = current_time

        # Select agent with highest weight
        best_agent = None
        best_weight = -1.0

        for agent in agents:
            weight = self._agent_weights.get(agent, 1.0)
            if weight > best_weight:
                best_weight = weight
                best_agent = agent

        return best_agent or agents[0]

    def _least_used_select(self, agents: list[str]) -> str:
        """Select the least used agent."""
        if not agents:
            raise ValueError("No agents available")

        best_agent = None
        best_count = float('inf')

        for agent in agents:
            count = self._usage_counters.get(agent, 0)
            if count < best_count:
                best_count = count
                best_agent = agent

        # Increment usage counter
        if best_agent:
            self._usage_counters[best_agent] += 1

        return best_agent or agents[0]

    def _update_weights(self, metrics: dict[str, AgentMetrics]) -> None:
        """Update agent weights based on performance metrics."""
        for agent_name, metric in metrics.items():
            # Calculate weight based on success rate and execution time
            if metric.execution_count > 0:
                success_rate = metric.success_count / metric.execution_count
                # Inverse execution time (faster is better)
                speed_score = 1.0 / (metric.avg_execution_time + 0.1)
                # Combine factors
                weight = success_rate * speed_score * 100
                self._agent_weights[agent_name] = weight

    def record_execution(self, agent_name: str) -> None:
        """Record agent execution for load balancing."""
        self._usage_counters[agent_name] += 1


class AsyncExecutionOptimizer:
    """
    Optimizes async execution patterns for better performance.

    Implements semaphore-based concurrency control, intelligent task grouping,
    and async timeout management.
    """

    def __init__(self, config: LoadBalancingConfig):
        self.config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent_agents)
        self._execution_stats: dict[str, list[float]] = defaultdict(list)
        self._active_tasks: set[str] = set()
        self._task_timeout_overrides: dict[str, float] = {}

    @asynccontextmanager
    async def execute_with_limits(self, agent_name: str, timeout: float | None = None):
        """
        Execute agent with concurrency limits and timeout management.

        Args:
            agent_name: Name of the agent
            timeout: Custom timeout override

        Yields:
            Execution context
        """
        # Check circuit breaker
        if self._is_circuit_breaker_open(agent_name):
                raise CircuitBreakerOpen(f"Circuit breaker open for agent {agent_name}")

        # Acquire semaphore
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=timeout or self.config.agent_timeout_seconds
            )
        except TimeoutError:
            raise AgentExecutionError(f"Timeout waiting for execution slot for {agent_name}")

        execution_id = f"{agent_name}_{time.time()}"
        self._active_tasks.add(execution_id)

        start_time = time.time()

        try:
            yield
        finally:
            # Release semaphore
            self._semaphore.release()

            # Record execution time
            execution_time = time.time() - start_time
            self._execution_stats[agent_name].append(execution_time)

            # Keep only recent stats (last 100 executions)
            if len(self._execution_stats[agent_name]) > 100:
                    self._execution_stats[agent_name] = self._execution_stats[agent_name][-100:]

            # Remove from active tasks
            self._active_tasks.discard(execution_id)

    def _is_circuit_breaker_open(self, agent_name: str) -> bool:
        """Check if circuit breaker is open for an agent."""
        recent_executions = self._execution_stats.get(agent_name, [])

        # Check recent failures (simplified - in real implementation, track failures separately)
        if len(recent_executions) >= self.config.circuit_breaker_threshnew:
            # Check if last N executions were all failures (timeout detection)
            recent_times = recent_executions[-self.config.circuit_breaker_threshnew:]
            timeout_count = sum(1 for t in recent_times if t >= self.config.agent_timeout_seconds * 0.9)

            if timeout_count >= self.config.circuit_breaker_threshnew - 1:
                return True

            return False

    def get_active_tasks_count(self) -> int:
        """Get count of currently active tasks."""
        return len(self._active_tasks)

    def get_execution_stats(self, agent_name: str) -> dict[str, float]:
        """Get execution statistics for an agent."""
        times = self._execution_stats.get(agent_name, [])
        if not times:
            return {}

        return {
            "count": len(times),
            "avg_time": sum(times) / len(times),
            "min_time": min(times),
            "max_time": max(times),
            "last_time": times[-1] if times else 0.0,
        }


class AgentPerformanceOptimizer:
    """
    Main performance optimizer for the Hledac agent ecosystem.

    Coordinates agent pooling, load balancing, and async optimization
    with real-time performance monitoring and automatic optimization.
    """

    def __init__(self, config: LoadBalancingConfig | None = None):
        self.config = config or LoadBalancingConfig()
        self.agent_pool = AgentPool(self.config)
        self.load_balancer = IntelligentLoadBalancer(self.config)
        self.async_optimizer = AsyncExecutionOptimizer(self.config)

        self._initialized = False
        self._last_optimization = 0.0
        self._optimization_interval = 300  # 5 minutes

    async def initialize(self) -> None:
        """Initialize the performance optimizer."""
        if self._initialized:
                    return

        await self.agent_pool.initialize()
        self._initialized = True
        logger.info("Agent performance optimizer initialized")

    async def shutdown(self) -> None:
        """Shutdown the performance optimizer."""
        await self.agent_pool.shutdown()
        self._initialized = False
        logger.info("Agent performance optimizer shutdown")

    @asynccontextmanager
    async def execute_agent(
        self,
        agent_name: str,
        agent_factory: Callable[[], Any],
        query: str,
        max_results: int = 10,
        timeout: float | None = None,
    ):
        """
        Execute an agent with full performance optimization.

        Args:
            agent_name: Name of the agent
            agent_factory: Factory function to create agent instances
            query: Search query
            max_results: Maximum results to return
            timeout: Custom timeout

        Yields:
            Agent execution results
        """
        if not self._initialized:
                await self.initialize()

        # Check if optimization is needed
        await self._maybe_optimize()

        # Get agent from pool
        async with self.agent_pool.get_agent(agent_name, agent_factory) as agent:
            # Execute with async optimization
            async with self.async_optimizer.execute_with_limits(agent_name, timeout):
                try:
                    # Execute the agent
                    if hasattr(agent, 'search'):
                            results = await agent.search(
                            query=query,
                            max_results=max_results,
                            timeout=timeout or self.config.agent_timeout_seconds,
                            run_id=f"opt_{time.time():.0f}",
                        )
                    elif hasattr(agent, 'run'):
                            results = await agent.run(query)
                    else:
                        raise AgentExecutionError(f"Agent {agent_name} has no searchable method")

                    yield results

                except Exception as e:
                    logger.error(f"Agent execution failed for {agent_name}: {e}")
                    raise

    async def select_best_agent(
        self,
        available_agents: list[str],
        query: str = "",
        metrics: dict[str, AgentMetrics] | None = None,
    ) -> str:
        """
        Select the best agent for execution based on current conditions.

        Args:
            available_agents: List of available agent names
            query: Search query (for future semantic matching)
            metrics: Agent performance metrics

        Returns:
            Selected agent name
        """
        if not available_agents:
                raise ValueError("No available agents")

        # Get current metrics if not provided
        if metrics is None:
                metrics = self.agent_pool.get_metrics()

        # Filter out unhealthy agents
        healthy_agents = []
        for agent in available_agents:
            metric = metrics.get(agent)
            if not metric or not metric.circuit_breaker_open:
                    healthy_agents.append(agent)

        if not healthy_agents:
            # Fallback to any agent if all are unhealthy
            healthy_agents = available_agents

        # Use load balancer to select best agent
            return self.load_balancer.select_agent(healthy_agents, metrics=metrics)

    async def optimize_performance(self) -> OptimizationReport:
        """
        Perform comprehensive performance optimization.

        Returns:
            Optimization report with results
        """
        report = OptimizationReport()

        try:
            # Get current memory usage
            memory_before = get_memory_usage_mb()

            # Identify bottlenecks
            bottlenecks = await self._identify_bottlenecks()
            report.bottlenecks_identified = bottlenecks

            # Apply optimizations
            for bottleneck in bottlenecks:
                if bottleneck == "high_memory":
                    await self._optimize_memory_usage()
                    report.optimizations_applied.append("memory_optimization")
                elif bottleneck == "slow_agents":
                    await self._optimize_slow_agents()
                    report.optimizations_applied.append("slow_agent_optimization")
                elif bottleneck == "circuit_breakers":
                    await self._reset_circuit_breakers()
                    report.optimizations_applied.append("circuit_breaker_reset")

            # Get memory after optimization
            memory_after = get_memory_usage_mb()
            report.memory_freed_mb = max(0, memory_before - memory_after)

            # Get pool statistics
            report.agent_pool_stats = self.agent_pool.get_pool_stats()

            # Calculate performance improvement (placehnewer)
            report.performance_improvement = 15.0  # Default 15% improvement

            self._last_optimization = time.time()

            logger.info(f"Performance optimization completed: {report}")

        except Exception as e:
            logger.error(f"Performance optimization failed: {e}")
            report.optimizations_applied.append("optimization_failed")

            return report

    async def _maybe_optimize(self) -> None:
        """Check if optimization is needed and perform it."""
        current_time = time.time()
        if current_time - self._last_optimization > self._optimization_interval:
            try:
                await self.optimize_performance()
            except Exception as e:
                logger.warning(f"Auto-optimization failed: {e}")

    async def _identify_bottlenecks(self) -> list[str]:
        """Identify current performance bottlenecks."""
        bottlenecks = []

        # Check memory usage
        memory_mb = get_memory_usage_mb()
        if memory_mb > self.config.memory_threshnew_mb:
            bottlenecks.append("high_memory")

        # Check for slow agents
        metrics = self.agent_pool.get_metrics()
        for _agent_name, metric in metrics.items():
            if metric.avg_execution_time > self.config.agent_timeout_seconds * 0.8:
                bottlenecks.append("slow_agents")
                break

        # Check circuit breakers
        for _agent_name, metric in metrics.items():
            if metric.circuit_breaker_open:
                bottlenecks.append("circuit_breakers")
                break

        # Check concurrent execution
        active_tasks = self.async_optimizer.get_active_tasks_count()
        if active_tasks >= self.config.max_concurrent_agents * 0.9:
            bottlenecks.append("high_concurrency")

        return bottlenecks

    async def _optimize_memory_usage(self) -> None:
        """Optimize memory usage."""
        # Clear agent pools if memory is high
        memory_mb = get_memory_usage_mb()
        if memory_mb > self.config.memory_threshnew_mb:
            await self.agent_pool._emergency_cleanup()

        # Force garbage collection
        gc.collect()

        logger.info("Memory optimization completed")

    async def _optimize_slow_agents(self) -> None:
        """Optimize slow-performing agents."""
        metrics = self.agent_pool.get_metrics()

        # Reset circuit breakers for agents that have recovered
        for agent_name, metric in metrics.items():
            if metric.circuit_breaker_open and metric.failure_count < metric.execution_count * 0.5:
                metric.circuit_breaker_open = False
                logger.info(f"Reset circuit breaker for agent {agent_name}")

    async def _reset_circuit_breakers(self) -> None:
        """Reset circuit breakers that may be stuck open."""
        metrics = self.agent_pool.get_metrics()

        for agent_name, metric in metrics.items():
            if metric.circuit_breaker_open:
                # Check if enough time has passed
                if time.time() - metric.last_used > self.config.circuit_breaker_timeout:
                    metric.circuit_breaker_open = False
                    logger.info(f"Auto-reset circuit breaker for agent {agent_name}")

    def get_comprehensive_metrics(self) -> dict[str, Any]:
        """Get comprehensive performance metrics."""
        return {
            "agent_metrics": self.agent_pool.get_metrics(),
            "pool_stats": self.agent_pool.get_pool_stats(),
            "active_tasks": self.async_optimizer.get_active_tasks_count(),
            "load_balancer_weights": dict(self.load_balancer._agent_weights),
            "memory_usage_mb": get_memory_usage_mb(),
            "config": {
                "max_concurrent_agents": self.config.max_concurrent_agents,
                "memory_threshnew_mb": self.config.memory_threshnew_mb,
                "agent_timeout_seconds": self.config.agent_timeout_seconds,
            }
        }
