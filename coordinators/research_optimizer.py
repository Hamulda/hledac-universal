"""
Research Optimizer - Performance Optimization for Research Operations

Optimizes research workflows through:
- Query optimization and deduplication
- Result caching with intelligent eviction
- Parallel execution strategies
- Resource usage optimization
- Adaptive timeout management

Based on crypto_optimization_engine concept from integration files.
"""
import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from enum import Enum
from typing import Any
from hledac.universal.utils.async_helpers import safe_gather_ok
logger = logging.getLogger(__name__)

class OptimizationStrategy(Enum):
    """Optimization strategies."""
    AGGRESSIVE = 'aggressive'
    BALANCED = 'balanced'
    CONSERVATIVE = 'conservative'
    ADAPTIVE = 'adaptive'

class CachePolicy(Enum):
    """Cache policies."""
    NO_CACHE = 'no_cache'
    MEMORY_ONLY = 'memory_only'
    PERSISTENT = 'persistent'

@dataclass(slots=True)
class OptimizationConfig:
    """Configuration for research optimization."""
    strategy: OptimizationStrategy = OptimizationStrategy.BALANCED
    cache_policy: CachePolicy = CachePolicy.MEMORY_ONLY
    max_concurrent_requests: int = 5
    default_timeout: float = 30.0
    adaptive_timeout: bool = True
    query_deduplication: bool = True
    result_batching: bool = True
    batch_size: int = 10
    memory_limit_mb: float = 500.0

@dataclass(frozen=True, slots=True)
class QueryMetrics:
    """Metrics for a query type."""
    query_hash: str
    count: int = 0
    avg_duration: float = 0.0
    success_rate: float = 1.0
    last_executed: float | None = None

@dataclass(frozen=True, slots=True)
class OptimizedResult:
    """Result with optimization metadata."""
    data: Any
    cache_hit: bool
    duration: float
    optimizations_applied: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

class ResearchOptimizer:
    """
    Research optimizer with caching, deduplication, and adaptive strategies.

    Example:
        >>> optimizer = ResearchOptimizer(OptimizationConfig(
        ...     strategy=OptimizationStrategy.BALANCED,
        ...     cache_policy=CachePolicy.MEMORY_ONLY
        ... ))
        >>>
        >>> # Optimized research
        >>> result = await optimizer.execute(
        ...     query="machine learning",
        ...     research_func=actual_research
        ... )
    """
    __slots__ = tuple(('_active_requests', '_cache', '_execution_times', '_in_flight', '_max_history', '_query_metrics', '_request_semaphore', 'config'))

    def __init__(self, config: OptimizationConfig | None=None):
        self.config = config or OptimizationConfig()
        self._cache: dict[str, tuple[Any, float]] = {}
        self._query_metrics: dict[str, QueryMetrics] = {}
        self._in_flight: dict[str, asyncio.Future] = {}
        self._active_requests = 0
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        self._request_semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        self._execution_times: list[float] = []
        self._max_history = 1000
        logger.info(f'ResearchOptimizer initialized ({self.config.strategy.value})')

    async def execute(self, query: str, research_func: Callable, **kwargs) -> OptimizedResult:
        """
        Execute research with optimizations.

        Args:
            query: Research query
            research_func: Research function to execute
            **kwargs: Additional arguments

        Returns:
            Optimized result with metadata
        """
        optimizations = []
        start_time = time.time()
        normalized = self._normalize_query(query)
        query_hash = self._hash_query(normalized)
        if self.config.cache_policy != CachePolicy.NO_CACHE:
            cached = self._get_from_cache(query_hash)
            if cached is not None:
                return OptimizedResult(data=cached, cache_hit=True, duration=time.time() - start_time, optimizations_applied=['cache_hit'], metadata={'query_hash': query_hash})
            optimizations.append('cache_checked')
        if self.config.query_deduplication and query_hash in self._in_flight:
            optimizations.append('deduplicated')
            try:
                data = await self._in_flight[query_hash]
                return OptimizedResult(data=data, cache_hit=False, duration=time.time() - start_time, optimizations_applied=optimizations, metadata={'query_hash': query_hash, 'deduplicated': True})
            except Exception:
                pass
        future = asyncio.Future()
        if self.config.query_deduplication:
            self._in_flight[query_hash] = future
        try:
            async with self._request_semaphore:
                timeout = self._calculate_timeout(query_hash)
                try:
                    async with asyncio.timeout(timeout):
                        data = await research_func(query, **kwargs)
                    optimizations.append(f'timeout_{timeout}s')
                except TimeoutError:
                    future.set_exception(TimeoutError(f'Query timed out after {timeout}s'))
                    raise
                duration = time.time() - start_time
                self._update_metrics(query_hash, duration, success=True)
                if self.config.cache_policy != CachePolicy.NO_CACHE:
                    self._cache_result(query_hash, data)
                    optimizations.append('cached')
                if self.config.query_deduplication:
                    future.set_result(data)
                    del self._in_flight[query_hash]
                return OptimizedResult(data=data, cache_hit=False, duration=duration, optimizations_applied=optimizations, metadata={'query_hash': query_hash, 'timeout': timeout})
        except Exception as e:
            self._update_metrics(query_hash, time.time() - start_time, success=False)
            if self.config.query_deduplication and query_hash in self._in_flight:
                if not future.done():
                    future.set_exception(e)
                del self._in_flight[query_hash]
            raise

    async def execute_batch(self, queries: list[str], research_func: Callable, **kwargs) -> list[OptimizedResult]:
        """
        Execute multiple queries with batching optimization.

        Args:
            queries: List of research queries
            research_func: Research function
            **kwargs: Additional arguments

        Returns:
            List of optimized results
        """
        if not self.config.result_batching or len(queries) <= self.config.batch_size:
            tasks = [self.execute(q, research_func, **kwargs) for q in queries]
            return await safe_gather_ok(*tasks, label='research_optimizer:247')
        all_results = []
        for i in range(0, len(queries), self.config.batch_size):
            batch = queries[i:i + self.config.batch_size]
            unique_queries = list(dict.fromkeys(batch))
            tasks = [self.execute(q, research_func, **kwargs) for q in unique_queries]
            batch_results = await safe_gather_ok(*tasks, label='research_optimizer:261')
            result_map = dict(zip(unique_queries, batch_results, strict=False))
            for q in batch:
                all_results.append(result_map[q])
        return all_results

    def _normalize_query(self, query: str) -> str:
        """Normalize query for deduplication."""
        normalized = ' '.join(query.lower().split())
        stop_words = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and'}
        words = normalized.split()
        normalized = ' '.join((w for w in words if w not in stop_words))
        return normalized

    def _hash_query(self, query: str) -> str:
        """Create hash of normalized query."""
        return hashlib.sha256(query.encode()).hexdigest()[:16]

    def _get_from_cache(self, query_hash: str) -> Any | None:
        """Get result from cache if valid."""
        if query_hash not in self._cache:
            return None
        result, expires_at = self._cache[query_hash]
        if time.time() > expires_at:
            del self._cache[query_hash]
            return None
        return result

    def _cache_result(self, query_hash: str, data: Any) -> None:
        """Cache result with TTL."""
        ttl_seconds = {OptimizationStrategy.AGGRESSIVE: 300, OptimizationStrategy.BALANCED: 600, OptimizationStrategy.CONSERVATIVE: 120, OptimizationStrategy.ADAPTIVE: 300}
        ttl = ttl_seconds[self.config.strategy]
        expires_at = time.time() + ttl
        self._cache[query_hash] = (data, expires_at)
        if len(self._cache) > 10000:
            self._cleanup_cache()

    def _cleanup_cache(self) -> None:
        """Remove expired cache entries."""
        now = time.time()
        expired = [k for k, (_, expires_at) in self._cache.items() if now > expires_at]
        for k in expired:
            del self._cache[k]
        logger.debug(f'Cache cleanup: removed {len(expired)} expired entries')

    def _calculate_timeout(self, query_hash: str) -> float:
        """Calculate adaptive timeout based on history."""
        if not self.config.adaptive_timeout:
            return self.config.default_timeout
        base_timeout = self.config.default_timeout
        if query_hash in self._query_metrics:
            metrics = self._query_metrics[query_hash]
            if metrics.avg_duration > base_timeout * 0.8:
                return min(base_timeout * 1.5, 120.0)
            if metrics.avg_duration < base_timeout * 0.3 and metrics.success_rate > 0.95:
                return max(base_timeout * 0.7, 5.0)
        return base_timeout

    def _update_metrics(self, query_hash: str, duration: float, success: bool) -> None:
        """Update query metrics."""
        if query_hash not in self._query_metrics:
            self._query_metrics[query_hash] = QueryMetrics(query_hash=query_hash)
        metrics = self._query_metrics[query_hash]
        metrics.count += 1
        metrics.last_executed = time.time()
        metrics.avg_duration = (metrics.avg_duration * (metrics.count - 1) + duration) / metrics.count
        success_val = 1.0 if success else 0.0
        metrics.success_rate = 0.9 * metrics.success_rate + 0.1 * success_val
        self._execution_times.append(duration)
        if len(self._execution_times) > self._max_history:
            self._execution_times = self._execution_times[-self._max_history:]

    def get_stats(self) -> dict[str, Any]:
        """Get optimizer statistics."""
        if not self._execution_times:
            avg_time = 0
            max_time = 0
        else:
            avg_time = sum(self._execution_times) / len(self._execution_times)
            max_time = max(self._execution_times)
        return {'config': {'strategy': self.config.strategy.value, 'cache_policy': self.config.cache_policy.value, 'max_concurrent': self.config.max_concurrent_requests}, 'cache': {'size': len(self._cache), 'active_in_flight': len(self._in_flight)}, 'performance': {'total_executions': len(self._execution_times), 'avg_duration': avg_time, 'max_duration': max_time, 'unique_queries': len(self._query_metrics)}, 'query_patterns': sorted([{'hash': m.query_hash[:8], 'count': m.count, 'avg_duration': m.avg_duration, 'success_rate': m.success_rate} for m in self._query_metrics.values()], key=lambda x: x['count'], reverse=True)[:10]}

    def clear_cache(self) -> int:
        """Clear all cached results. Returns count of cleared entries."""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f'Cache cleared: {count} entries removed')
        return count

async def optimized_research(query: str, research_func: Callable, strategy: OptimizationStrategy=OptimizationStrategy.BALANCED, **kwargs) -> OptimizedResult:
    """
    Quick optimized research.

    Args:
        query: Research query
        research_func: Research function
        strategy: Optimization strategy
        **kwargs: Additional arguments

    Returns:
        Optimized result
    """
    optimizer = ResearchOptimizer(OptimizationConfig(strategy=strategy))
    return await optimizer.execute(query, research_func, **kwargs)

def create_optimized_pipeline(strategy: OptimizationStrategy=OptimizationStrategy.BALANCED) -> tuple[ResearchOptimizer, PrivacyEnhancedResearch]:
    """
    Create optimized privacy-enhanced research pipeline.

    Returns:
        Tuple of (optimizer, privacy_research)
    """
    from .privacy_enhanced_research import PrivacyConfig, PrivacyEnhancedResearch
    optimizer = ResearchOptimizer(OptimizationConfig(strategy=strategy))
    privacy = PrivacyEnhancedResearch(PrivacyConfig())
    return (optimizer, privacy)