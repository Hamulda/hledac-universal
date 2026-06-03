"""
Agent Performance Benchmarks for Hledac

Comprehensive benchmarking suite for measuring and comparing agent performance
with focus on 8GB M1 constraints and realistic usage patterns.

Key Features:
- Multi-agent benchmark execution
- Memory usage monitoring
- Latency and throughput measurement
- Resource utilization tracking
- Performance regression detection
- Comparative analysis reporting
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
import tracemalloc
from collections import deque
from dataclasses import dataclass, field
from typing import Any

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

# Snapshot history bound for MemoryProfiler - prevents unbounded memory growth
MAX_SNAPSHOT_HISTORY: int = 1000

# Optional imports
try:
    from hledac.models import SearchResult
except ImportError:
    SearchResult = None

try:
    from hledac.runtime.unified_orchestrator import AgentProtocol
except ImportError:
    AgentProtocol = Any

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for agent benchmarking."""
    warmup_iterations: int = 3
    benchmark_iterations: int = 10
    max_concurrent_agents: int = 4
    timeout_seconds: float = 30.0
    memory_threshnew_mb: int = 512
    sample_queries: list[str] = field(default_factory=lambda: [
        "machine learning algorithms",
        "quantum computing research",
        "climate change impact",
        "renewable energy systems",
        "artificial intelligence ethics"
    ])


@dataclass
class AgentBenchmarkResult:
    """Results from benchmarking a single agent."""
    agent_name: str
    total_executions: int
    successful_executions: int
    failed_executions: int
    success_rate: float

    # Timing metrics (in seconds)
    avg_execution_time: float
    min_execution_time: float
    max_execution_time: float
    p95_execution_time: float
    p99_execution_time: float

    # Memory metrics (in MB)
    avg_memory_usage: float
    peak_memory_usage: float
    memory_growth_rate: float

    # Throughput metrics
    avg_results_per_second: float
    total_results_returned: int

    # Performance classification
    performance_tier: str  # "excellent", "good", "average", "poor"
    bottlenecks: list[str] = field(default_factory=list)

    # Raw data for analysis
    execution_times: list[float] = field(default_factory=list)
    memory_snapshots: list[float] = field(default_factory=list)


@dataclass
class BenchmarkReport:
    """Comprehensive benchmark report for multiple agents."""
    timestamp: float = field(default_factory=time.time)
    config: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    agent_results: dict[str, AgentBenchmarkResult] = field(default_factory=dict)
    system_metrics: dict[str, Any] = field(default_factory=dict)
    comparative_analysis: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    # Summary statistics
    total_agents_tested: int = 0
    overall_success_rate: float = 0.0
    avg_execution_time: float = 0.0
    total_memory_usage: float = 0.0
    performance_improvement_potential: float = 0.0


class MemoryProfiler:
    """Memory profiling utility for benchmarking."""

    def __init__(self):
        self._snapshots: deque[tuple[float, float]] = deque(maxlen=MAX_SNAPSHOT_HISTORY)  # (timestamp, memory_mb)
        self._active = False
        self._process = psutil.Process() if PSUTIL_AVAILABLE else None

    def start_profiling(self) -> None:
        """Start memory profiling."""
        self._snapshots.clear()
        self._active = True
        tracemalloc.start()

        # Start memory monitoring task - guard against missing event loop
        try:
            asyncio.create_task(self._monitor_memory(), name="benchmark:memory_monitor")
        except RuntimeError:
            # No running event loop - profiling not available
            self._active = False
            tracemalloc.stop()

    def stop_profiling(self) -> tuple[float, float, float]:
        """Stop profiling and return memory statistics."""
        self._active = False

        if not self._snapshots:
                    return 0.0, 0.0, 0.0

        # Calculate statistics
        memory_values = [snapshot[1] for snapshot in self._snapshots]
        avg_memory = sum(memory_values) / len(memory_values)
        peak_memory = max(memory_values)

        # Calculate growth rate
        if len(self._snapshots) >= 2:
            start_memory = self._snapshots[0][1]
            end_memory = self._snapshots[-1][1]
            duration = self._snapshots[-1][0] - self._snapshots[0][0]
            growth_rate = (end_memory - start_memory) / max(duration, 1.0)
        else:
            growth_rate = 0.0

        # Get tracemalloc statistics
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        return avg_memory, peak_memory, growth_rate

    async def _monitor_memory(self) -> None:
        """Monitor memory usage in background."""
        while self._active:
            try:
                if self._process is None:
                    break
                memory_mb = self._process.memory_info().rss / 1024 / 1024
                self._snapshots.append((time.time(), memory_mb))
                await asyncio.sleep(0.1)  # Sample every 100ms
            except Exception:
                break


class AgentBenchmarker:
    """
    Comprehensive agent benchmarking system.

    Measures agent performance across multiple dimensions including
    execution time, memory usage, throughput, and reliability.
    """

    def __init__(self, config: BenchmarkConfig | None = None):
        self.config = config or BenchmarkConfig()
        self.memory_profiler = MemoryProfiler()
        self._system_baseline: dict[str, Any] | None = None

    async def benchmark_agent(
        self,
        agent_name: str,
        agent: AgentProtocol,
        custom_queries: list[str] | None = None
    ) -> AgentBenchmarkResult:
        """
        Benchmark a single agent comprehensively.

        Args:
            agent_name: Name of the agent
            agent: Agent instance
            custom_queries: Custom queries to test (optional)

        Returns:
            Comprehensive benchmark results
        """
        logger.info(f"Starting benchmark for agent: {agent_name}")

        queries = custom_queries or self.config.sample_queries
        execution_times: list[float] = []
        memory_snapshots: list[float] = []
        successful_executions = 0
        total_results = 0

        # Warmup phase
        await self._warmup_agent(agent, queries[:2])

        # Benchmark phase
        for iteration in range(self.config.benchmark_iterations):
            query = queries[iteration % len(queries)]

            try:
                # Start memory profiling
                self.memory_profiler.start_profiling()

                start_time = time.time()

                # Execute agent
                if hasattr(agent, 'search'):
                        results = await agent.search(
                        query=query,
                        max_results=10,
                        timeout=self.config.timeout_seconds,
                        run_id=f"benchmark_{agent_name}_{iteration}",
                    )
                elif hasattr(agent, 'run'):
                        results = await agent.run(query)
                else:
                    raise ValueError(f"Agent {agent_name} has no executable method")

                execution_time = time.time() - start_time
                execution_times.append(execution_time)

                # Stop memory profiling
                avg_memory, peak_memory, growth_rate = self.memory_profiler.stop_profiling()
                memory_snapshots.append(avg_memory)

                # Count results
                if results:
                    if isinstance(results, list):
                        total_results += len(results)
                    else:
                        total_results += 1

                successful_executions += 1

                logger.debug(f"Benchmark iteration {iteration + 1} completed for {agent_name}")

            except Exception as e:
                logger.warning(f"Benchmark iteration {iteration + 1} failed for {agent_name}: {e}")
                execution_times.append(self.config.timeout_seconds)  # Max time for failures
                memory_snapshots.append(0.0)

            # Cleanup between iterations
            await self._cleanup_between_iterations()

        # Calculate results
        result = self._calculate_benchmark_results(
            agent_name, execution_times, memory_snapshots,
            successful_executions, total_results, self.config.benchmark_iterations
        )

        logger.info(f"Benchmark completed for {agent_name}: {result.performance_tier} tier")
        return result

    async def benchmark_multiple_agents(
        self,
        agents: dict[str, AgentProtocol],
        concurrent: bool = False
    ) -> BenchmarkReport:
        """
        Benchmark multiple agents and generate comparative report.

        Args:
            agents: Dictionary of agent name to agent instance
            concurrent: Whether to run benchmarks concurrently

        Returns:
            Comprehensive benchmark report
        """
        logger.info(f"Starting benchmark for {len(agents)} agents (concurrent={concurrent})")

        # Capture system baseline
        self._system_baseline = await self._capture_system_baseline()

        report = BenchmarkReport(config=self.config)
        report.total_agents_tested = len(agents)

        if concurrent:
            # Run benchmarks concurrently
            tasks = []
            for agent_name, agent in agents.items():
                task = asyncio.create_task(
                    self.benchmark_agent(agent_name, agent),
                    name=f"benchmark:agent:{agent_name}"
                )
                tasks.append((agent_name, task))

            # Wait for all benchmarks to complete
            for agent_name, task in tasks:
                try:
                    result = await task
                    report.agent_results[agent_name] = result
                except Exception as e:
                    logger.error(f"Benchmark failed for agent {agent_name}: {e}")
                    # Create failed result
                    failed_result = AgentBenchmarkResult(
                        agent_name=agent_name,
                        total_executions=self.config.benchmark_iterations,
                        successful_executions=0,
                        failed_executions=self.config.benchmark_iterations,
                        success_rate=0.0,
                        avg_execution_time=self.config.timeout_seconds,
                        min_execution_time=self.config.timeout_seconds,
                        max_execution_time=self.config.timeout_seconds,
                        p95_execution_time=self.config.timeout_seconds,
                        p99_execution_time=self.config.timeout_seconds,
                        avg_memory_usage=0.0,
                        peak_memory_usage=0.0,
                        memory_growth_rate=0.0,
                        avg_results_per_second=0.0,
                        total_results_returned=0,
                        performance_tier="failed",
                        bottlenecks=["execution_failure"]
                    )
                    report.agent_results[agent_name] = failed_result
        else:
            # Run benchmarks sequentially
            for agent_name, agent in agents.items():
                try:
                    result = await self.benchmark_agent(agent_name, agent)
                    report.agent_results[agent_name] = result
                except Exception as e:
                    logger.error(f"Benchmark failed for agent {agent_name}: {e}")

        # Generate comparative analysis
        report.comparative_analysis = self._generate_comparative_analysis(report.agent_results)
        report.system_metrics = await self._capture_system_metrics()

        # Calculate summary statistics
        report = self._calculate_summary_statistics(report)

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        logger.info(f"Benchmark suite completed: {len(report.agent_results)} agents tested")
        return report

    async def _warmup_agent(self, agent: AgentProtocol, queries: list[str]) -> None:
        """Warm up agent with a few queries."""
        for i, query in enumerate(queries[:self.config.warmup_iterations]):
            try:
                if hasattr(agent, 'search'):
                        await agent.search(
                        query=query,
                        max_results=5,  # Small results for warmup
                        timeout=self.config.timeout_seconds,
                        run_id=f"warmup_{i}",
                    )
                elif hasattr(agent, 'run'):
                        await agent.run(query)
            except Exception as e:
                logger.debug(f"Warmup query {i + 1} failed: {e}")

    async def _cleanup_between_iterations(self) -> None:
        """Cleanup between benchmark iterations."""
        # Force garbage collection
        gc.collect()

        # Small delay to allow system cleanup
        await asyncio.sleep(0.1)

    def _calculate_benchmark_results(
        self,
        agent_name: str,
        execution_times: list[float],
        memory_snapshots: list[float],
        successful_executions: int,
        total_results: int,
        total_executions: int
    ) -> AgentBenchmarkResult:
        """Calculate comprehensive benchmark results."""

        if not execution_times:
            # Create failed result
                return AgentBenchmarkResult(
                agent_name=agent_name,
                total_executions=total_executions,
                successful_executions=0,
                failed_executions=total_executions,
                success_rate=0.0,
                avg_execution_time=0.0,
                min_execution_time=0.0,
                max_execution_time=0.0,
                p95_execution_time=0.0,
                p99_execution_time=0.0,
                avg_memory_usage=0.0,
                peak_memory_usage=0.0,
                memory_growth_rate=0.0,
                avg_results_per_second=0.0,
                total_results_returned=0,
                performance_tier="failed",
                bottlenecks=["no_executions"]
            )

        # Execution time statistics
        avg_time = sum(execution_times) / len(execution_times)
        min_time = min(execution_times)
        max_time = max(execution_times)
        sorted_times = sorted(execution_times)
        p95_time = sorted_times[int(0.95 * len(sorted_times))]
        p99_time = sorted_times[int(0.99 * len(sorted_times))]

        # Memory statistics
        if memory_snapshots:
            avg_memory = sum(memory_snapshots) / len(memory_snapshots)
            peak_memory = max(memory_snapshots)
            memory_growth = memory_snapshots[-1] - memory_snapshots[0] if len(memory_snapshots) > 1 else 0.0
        else:
            avg_memory = peak_memory = memory_growth = 0.0

        # Success rate
        success_rate = successful_executions / total_executions if total_executions > 0 else 0.0

        # Throughput
        total_time = sum(execution_times)
        avg_results_per_second = total_results / total_time if total_time > 0 else 0.0

        # Determine performance tier
        performance_tier = self._determine_performance_tier(
            avg_time, success_rate, avg_memory, avg_results_per_second
        )

        # Identify bottlenecks
        bottlenecks = self._identify_bottlenecks(
            avg_time, success_rate, avg_memory, memory_growth
        )

        return AgentBenchmarkResult(
            agent_name=agent_name,
            total_executions=total_executions,
            successful_executions=successful_executions,
            failed_executions=total_executions - successful_executions,
            success_rate=success_rate,
            avg_execution_time=avg_time,
            min_execution_time=min_time,
            max_execution_time=max_time,
            p95_execution_time=p95_time,
            p99_execution_time=p99_time,
            avg_memory_usage=avg_memory,
            peak_memory_usage=peak_memory,
            memory_growth_rate=memory_growth,
            avg_results_per_second=avg_results_per_second,
            total_results_returned=total_results,
            performance_tier=performance_tier,
            bottlenecks=bottlenecks,
            execution_times=execution_times,
            memory_snapshots=memory_snapshots
        )

    def _determine_performance_tier(
        self,
        avg_time: float,
        success_rate: float,
        avg_memory: float,
        throughput: float
    ) -> str:
        """Determine performance tier based on metrics."""
        score = 0

        # Success rate scoring (40% weight)
        if success_rate >= 0.95:
                score += 40
        elif success_rate >= 0.85:
                score += 30
        elif success_rate >= 0.70:
                score += 20
        elif success_rate >= 0.50:
                score += 10

        # Execution time scoring (30% weight)
        if avg_time <= 2.0:
                score += 30
        elif avg_time <= 5.0:
                score += 25
        elif avg_time <= 10.0:
                score += 20
        elif avg_time <= 20.0:
                score += 15
        elif avg_time <= 30.0:
                score += 10

        # Memory usage scoring (20% weight)
        if avg_memory <= 50:
                score += 20
        elif avg_memory <= 100:
                score += 15
        elif avg_memory <= 200:
                score += 10
        elif avg_memory <= 400:
                score += 5

        # Throughput scoring (10% weight)
        if throughput >= 5.0:
                score += 10
        elif throughput >= 2.0:
                score += 8
        elif throughput >= 1.0:
                score += 6
        elif throughput >= 0.5:
                score += 4
        elif throughput >= 0.1:
                score += 2

        # Determine tier
        if score >= 85:
                    return "excellent"
        elif score >= 70:
                    return "good"
        elif score >= 50:
                    return "average"
        elif score >= 30:
                    return "poor"
        else:
                return "failed"

    def _identify_bottlenecks(
        self,
        avg_time: float,
        success_rate: float,
        avg_memory: float,
        memory_growth: float
    ) -> list[str]:
        """Identify performance bottlenecks."""
        bottlenecks = []

        if success_rate < 0.8:
                bottlenecks.append("low_reliability")

        if avg_time > 20.0:
                bottlenecks.append("slow_execution")

        if avg_memory > 200:
                bottlenecks.append("high_memory_usage")

        if memory_growth > 10.0:  # MB per execution
            bottlenecks.append("memory_leak")

        if avg_time > 30.0 or success_rate < 0.5:
            bottlenecks.append("critical_performance_issue")

        return bottlenecks

    def _generate_comparative_analysis(
        self,
        results: dict[str, AgentBenchmarkResult]
    ) -> dict[str, Any]:
        """Generate comparative analysis of benchmark results."""
        if not results:
            return {}

        # Performance tiers distribution
        tier_counts = {"excellent": 0, "good": 0, "average": 0, "poor": 0, "failed": 0}
        for result in results.values():
            tier_counts[result.performance_tier] += 1

        # Top performers
        sorted_by_speed = sorted(
            results.items(),
            key=lambda x: x[1].avg_execution_time
        )
        sorted_by_reliability = sorted(
            results.items(),
            key=lambda x: x[1].success_rate,
            reverse=True
        )
        sorted_by_memory = sorted(
            results.items(),
            key=lambda x: x[1].avg_memory_usage
        )

        # Bottleneck analysis
        all_bottlenecks = []
        for result in results.values():
            all_bottlenecks.extend(result.bottlenecks)

        bottleneck_counts = {}
        for bottleneck in all_bottlenecks:
            bottleneck_counts[bottleneck] = bottleneck_counts.get(bottleneck, 0) + 1

            return {
            "performance_tier_distribution": tier_counts,
            "fastest_agents": [(name, result.avg_execution_time) for name, result in sorted_by_speed[:5]],
            "most_reliable_agents": [(name, result.success_rate) for name, result in sorted_by_reliability[:5]],
            "most_memory_efficient": [(name, result.avg_memory_usage) for name, result in sorted_by_memory[:5]],
            "common_bottlenecks": bottleneck_counts,
            "performance_variance": self._calculate_performance_variance(results),
        }

    def _calculate_performance_variance(
        self,
        results: dict[str, AgentBenchmarkResult]
    ) -> dict[str, float]:
        """Calculate performance variance metrics."""
        if len(results) < 2:
                    return {"execution_time_variance": 0.0, "memory_variance": 0.0}

        execution_times = [result.avg_execution_time for result in results.values()]
        memory_usage = [result.avg_memory_usage for result in results.values()]

        # Calculate variance
        avg_time = sum(execution_times) / len(execution_times)
        avg_memory = sum(memory_usage) / len(memory_usage)

        time_variance = sum((t - avg_time) ** 2 for t in execution_times) / len(execution_times)
        memory_variance = sum((m - avg_memory) ** 2 for m in memory_usage) / len(memory_usage)

        return {
            "execution_time_variance": time_variance,
            "memory_variance": memory_variance,
            "execution_time_std": time_variance ** 0.5,
            "memory_std": memory_variance ** 0.5,
        }

    async def _capture_system_baseline(self) -> dict[str, Any]:
        """Capture system baseline metrics."""
        if not PSUTIL_AVAILABLE or psutil is None:
            return {"error": "psutil not available", "timestamp": time.time()}
        process = psutil.Process()
        return {
            "cpu_count": psutil.cpu_count(),
            "memory_total_gb": psutil.virtual_memory().total / 1024 / 1024 / 1024,
            "memory_available_gb": psutil.virtual_memory().available / 1024 / 1024 / 1024,
            "process_memory_mb": process.memory_info().rss / 1024 / 1024,
            "process_cpu_percent": process.cpu_percent(),
            "timestamp": time.time(),
        }

    async def _capture_system_metrics(self) -> dict[str, Any]:
        """Capture current system metrics."""
        if not PSUTIL_AVAILABLE or psutil is None:
            return {"error": "psutil not available", "timestamp": time.time()}
        process = psutil.Process()
        return {
            "memory_usage_mb": process.memory_info().rss / 1024 / 1024,
            "memory_percent": process.memory_percent(),
            "cpu_percent": process.cpu_percent(),
            "open_files": len(process.open_files()),
            "threads": process.num_threads(),
        }

    def _calculate_summary_statistics(self, report: BenchmarkReport) -> BenchmarkReport:
        """Calculate summary statistics for the report."""
        if not report.agent_results:
                    return report

        # Overall success rate
        total_success = sum(r.successful_executions for r in report.agent_results.values())
        total_executions = sum(r.total_executions for r in report.agent_results.values())
        report.overall_success_rate = total_success / total_executions if total_executions > 0 else 0.0

        # Average execution time
        all_times = [r.avg_execution_time for r in report.agent_results.values() if r.avg_execution_time > 0]
        report.avg_execution_time = sum(all_times) / len(all_times) if all_times else 0.0

        # Total memory usage
        report.total_memory_usage = sum(r.avg_memory_usage for r in report.agent_results.values())

        # Performance improvement potential (estimated)
        failed_agents = [r for r in report.agent_results.values() if r.performance_tier in ["poor", "failed"]]
        report.performance_improvement_potential = (len(failed_agents) / len(report.agent_results)) * 100

        return report

    def _generate_recommendations(self, report: BenchmarkReport) -> list[str]:
        """Generate optimization recommendations based on benchmark results."""
        recommendations = []

        if not report.agent_results:
                    return ["No benchmark results available for recommendations"]

        # Analyze overall performance
        if report.overall_success_rate < 0.8:
                recommendations.append("Improve overall reliability - many agents are failing")

        if report.avg_execution_time > 15.0:
                recommendations.append("Optimize execution times - average is slow")

        if report.total_memory_usage > len(report.agent_results) * 100:
                recommendations.append("Optimize memory usage - agents are consuming too much memory")

        # Analyze individual agent issues
        poor_agents = [name for name, result in report.agent_results.items()
                      if result.performance_tier in ["poor", "failed"]]

        if poor_agents:
                recommendations.append(f"Focus optimization on poorly performing agents: {', '.join(poor_agents[:3])}")

        # Common bottlenecks
        if report.comparative_analysis.get("common_bottlenecks"):
                common_issues = list(report.comparative_analysis["common_bottlenecks"].keys())
                if "low_reliability" in common_issues:
                    recommendations.append("Implement better error handling and retry mechanisms")
                if "slow_execution" in common_issues:
                    recommendations.append("Optimize network requests and implement caching")
                if "high_memory_usage" in common_issues:
                    recommendations.append("Implement memory pooling and cleanup strategies")
                if "memory_leak" in common_issues:
                    recommendations.append("Investigate and fix memory leaks in agents")

        # System-specific recommendations
        if report.system_metrics.get("memory_percent", 0) > 80:
                recommendations.append("Reduce memory usage - system is under memory pressure")

                return recommendations


# Utility functions for running benchmarks
async def run_agent_benchmarks(
    agents: dict[str, AgentProtocol],
    config: BenchmarkConfig | None = None,
    concurrent: bool = False
) -> BenchmarkReport:
    """
    Convenience function to run agent benchmarks.

    Args:
        agents: Dictionary of agent name to agent instance
        config: Benchmark configuration (optional)
        concurrent: Whether to run benchmarks concurrently

    Returns:
        Comprehensive benchmark report
    """
    benchmarker = AgentBenchmarker(config)
    return await benchmarker.benchmark_multiple_agents(agents, concurrent)


async def run_quick_performance_check(
    agents: dict[str, AgentProtocol],
    sample_size: int = 3
) -> dict[str, Any]:
    """
    Run a quick performance check on agents.

    Args:
        agents: Dictionary of agent name to agent instance
        sample_size: Number of sample queries to use

    Returns:
        Quick performance summary
    """
    config = BenchmarkConfig(
        warmup_iterations=1,
        benchmark_iterations=sample_size,
        sample_queries=["test query", "sample search", "example request"][:sample_size]
    )

    report = await run_agent_benchmarks(agents, config, concurrent=False)

    return {
        "summary": {
            "agents_tested": len(report.agent_results),
            "overall_success_rate": report.overall_success_rate,
            "avg_execution_time": report.avg_execution_time,
            "performance_tiers": report.comparative_analysis.get("performance_tier_distribution", {}),
        },
        "top_performers": {
            "fastest": report.comparative_analysis.get("fastest_agents", [])[:3],
            "most_reliable": report.comparative_analysis.get("most_reliable_agents", [])[:3],
        },
        "recommendations": report.recommendations[:5],  # Top 5 recommendations
        "detailed_results": {name: {
            "success_rate": result.success_rate,
            "avg_time": result.avg_execution_time,
            "memory_usage": result.avg_memory_usage,
            "performance_tier": result.performance_tier,
        } for name, result in report.agent_results.items()}
    }
