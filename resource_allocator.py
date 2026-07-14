"""
Resource Allocator with Predictive Modeling
==========================================

ROLE: Canonical REQUEST-LEVEL BUDGETING / CONCURRENCY PRIMITIVE (not a sampler or governor).

This module provides:
- Request-level RAM budgeting with MLX linear regression prediction
- Adaptive concurrency semaphore based on memory pressure
- Emergency brake (cancel lowest priority task)
- Concurrency limits that adapt to system memory pressure

AUTHORITY BOUNDARY:
- SAMPLER (utils/uma_budget.py): raw memory sampling, no policy
- GOVERNOR (core/resource_governor.py): policy/hysteresis/runtime governance
- ALLOCATOR (resource_allocator.py): request-level budgeting/concurrency

Note: get_memory_pressure_level() in this module uses percent-based thresholds
(pct > 85 → warn, pct > 93 → critical) which are independent from
uma_budget.py absolute-MB thresholds. These serve different purposes:
- uma_budget.py: absolute system+MLX used (Calibrated for M1 8GB UMA)
- resource_allocator.py: percent-based system pressure (for AdaptiveSemaphore decisions)
"""
import logging
import time
from dataclasses import dataclass
import msgspec
from typing import Any, Self
from core.psutil_shim import virtual_memory as _virtual_memory, PSUTIL_AVAILABLE as _PSUTIL_AVAILABLE
from hledac.universal.utils.uma_budget import M1_FETCH_SOFT_CEILING_GB
logger = logging.getLogger(__name__)
MLX_AVAILABLE = False
_FALLBACK_RAM_ESTIMATE_MB: float = 500.0

@dataclass(slots=True)
class ResourceBudget:
    """Resource budget for a request."""
    ram_mb: int
    time_sec: float
    priority: int
    request_id: str
    context: Any = None

class ResourceExhausted(Exception):
    """Raised when resources cannot be allocated."""
    pass

class ResourceAllocator:
    """
    Predictive resource allocator with:
    - MAX_CONCURRENT: Maximum concurrent requests (default 3)
    - MAX_RAM_GB: Maximum RAM usage (mirrors uma_budget.M1_FETCH_SOFT_CEILING_GB)
    - SOFT_PREEMPT_RAM_GIB: Request-level soft-preemption threshold (default 6.5 GB)
    - Warm-up: First 5 queries use fixed allocation
    - MLX-based linear regression for prediction after warm-up
    """
    MAX_CONCURRENT: int = 3
    MAX_RAM_GB: float = M1_FETCH_SOFT_CEILING_GB
    SOFT_PREEMPT_RAM_GIB: float = 6.5
    WARMUP_QUERIES: int = 5
    __slots__ = tuple(('active_requests', 'coeffs', 'history', 'total_ram_mb', 'warmup_counter'))

    def __init__(self):
        self.active_requests: dict[str, ResourceBudget] = {}
        self.total_ram_mb: float = 0.0
        self.history: list[tuple[list[float], float]] = []
        self.coeffs: Any | None = None
        self.warmup_counter: int = 0

    def _extract_features(self, ctx: Any) -> list[float]:
        """Extract feature vector for RAM prediction."""
        return [float(len(ctx.query)) if hasattr(ctx, 'query') else 100.0, float(ctx.depth) if hasattr(ctx, 'depth') else 1.0, float(len(getattr(ctx, 'selected_sources', []))), float(getattr(ctx, 'complexity_score', 0.5))]

    def _update_model(self):
        """Update MLX linear regression model from history."""
        if len(self.history) < self.WARMUP_QUERIES:
            self.warmup_counter += 1
            return
        try:
            import mlx.core as mx
            X = mx.array([f for f, _ in self.history])
            y = mx.array([a for _, a in self.history])
            ones = mx.ones((X.shape[0], 1))
            X = mx.concatenate([X, ones], axis=1)
            self.coeffs, _, _, _ = mx.linalg.lstsq(X, y, rcond=None)
            logger.debug(f'Updated MLX prediction model with {len(self.history)} samples')
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.warning(f'Failed to update MLX model: {e}')
            self.coeffs = None

    def predict_ram(self, ctx: Any) -> float:
        """Predict RAM usage for a context using MLX linear regression."""
        if self.coeffs is None:
            return _FALLBACK_RAM_ESTIMATE_MB
        try:
            import mlx.core as mx
            features = mx.array(self._extract_features(ctx) + [1.0])
            prediction = float(mx.sum(features * self.coeffs))
            return max(100.0, prediction)
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.warning(f'RAM prediction failed: {e}')
            return _FALLBACK_RAM_ESTIMATE_MB

    def can_accept(self, ctx: Any) -> bool:
        """Check if a new request can be accepted."""
        if len(self.active_requests) >= self.MAX_CONCURRENT:
            return False
        predicted = self.predict_ram(ctx)
        if self.total_ram_mb + predicted > self.MAX_RAM_GB * 1024:
            return False
        return True

    def acquire(self, request_id: str, ctx: Any, priority: int) -> ResourceBudget:
        """Acquire resources for a new request."""
        if not self.can_accept(ctx):
            raise ResourceExhausted(f'Cannot accept request {request_id}: resources exhausted')
        predicted = self.predict_ram(ctx)
        budget = ResourceBudget(ram_mb=int(predicted), time_sec=300.0, priority=priority, request_id=request_id, context=ctx)
        self.active_requests[request_id] = budget
        self.total_ram_mb += predicted
        logger.debug(f'Allocated {predicted:.0f} MB for request {request_id} (priority {priority})')
        return budget

    def release(self, request_id: str, actual_ram_mb: float):
        """Release resources and record actual usage for learning."""
        if request_id in self.active_requests:
            budget = self.active_requests.pop(request_id)
            self.total_ram_mb -= budget.ram_mb
            ctx = getattr(budget, 'context', None)
            if ctx is not None:
                features = self._extract_features(ctx)
                self.history.append((features, actual_ram_mb))
                if len(self.history) > 100:
                    self.history = self.history[-50:]
            self._update_model()
            logger.debug(f'Released request {request_id}, actual RAM: {actual_ram_mb:.0f} MB')

    def emergency_brake(self) -> str | None:
        """
        Emergency brake: cancel lowest priority task if RSS > SOFT_PREEMPT_RAM_GIB.
        Returns cancelled request_id or None.
        """
        try:
            if not _PSUTIL_AVAILABLE:
                return None
            mem = _virtual_memory()
            if mem is None:
                return None
            if mem.used < self.SOFT_PREEMPT_RAM_GIB * 1024 ** 3:
                return None
            if not self.active_requests:
                return None
            lowest = max(self.active_requests.values(), key=lambda b: b.priority)
            self.cancel(lowest.request_id)
            logger.warning(f'Emergency brake: cancelled {lowest.request_id} (RSS: {mem.used / 1024 ** 3:.2f} GB)')
            return lowest.request_id
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.error(f'Emergency brake failed: {e}')
            return None

    def cancel(self, request_id: str):
        """Cancel a specific request."""
        if request_id in self.active_requests:
            budget = self.active_requests.pop(request_id)
            self.total_ram_mb -= budget.ram_mb
            logger.info(f'Cancelled request {request_id}')

    def get_stats(self) -> dict[str, Any]:
        """Get current allocator statistics."""
        return {'active_requests': len(self.active_requests), 'total_ram_mb': self.total_ram_mb, 'warmup_counter': self.warmup_counter, 'history_size': len(self.history), 'model_ready': self.coeffs is not None}

def get_memory_pressure_level() -> str:
    """
    Legacy percent-based pressure level (backward compat wrapper).

    Now delegates to AdaptiveWorkerPool.get_uma_state() which uses
    M1ResourceGovernor with absolute GiB thresholds.

    Returns: "normal" | "warn" | "critical"  (mapped from UMA state)
    """
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        from utils.concurrency import AdaptiveWorkerPool
        pool = AdaptiveWorkerPool._instance
        if pool is not None:
            return _uma_state_to_pressure_level(pool.get_uma_state())
    except Exception:  # noqa: BLE001 — best-effort; UMA state check failure returns 'normal'
        pass
    return 'normal'

def _uma_state_to_pressure_level(state: str) -> str:
    """Map UMA state string to legacy pressure level string."""
    mapping = {'ok': 'normal', 'soft_warn': 'normal', 'warn': 'warn', 'critical': 'critical', 'emergency': 'critical'}
    return mapping.get(state, 'normal')

def get_recommended_concurrency() -> dict[str, int]:
    """
    Return concurrency limits based on memory pressure level.

    Now delegates to AdaptiveWorkerPool (M1ResourceGovernor) for ml_jobs
    and fetch limits. Falls back to legacy behavior if pool unavailable.
    """
    level = get_memory_pressure_level()
    pool = None
    try:
        from utils.concurrency import AdaptiveWorkerPool
        pool = AdaptiveWorkerPool._instance
    except Exception:  # noqa: BLE001 — best-effort; pool import failure falls back to legacy limits
        pass
    if pool is not None and level != 'normal':
        fetch = pool.get_fetch_limit()
        workers = pool.get_max_workers()
        if level == 'critical':
            import gc
            gc.collect()
        return {'fetch': max(4, fetch), 'parse_workers': max(1, workers), 'ml_jobs': workers, 'browser': 0 if level == 'critical' else 1}
    concurrency = {'normal': {'fetch': 20, 'parse_workers': 4, 'ml_jobs': 1, 'browser': 1}, 'warn': {'fetch': 8, 'parse_workers': 2, 'ml_jobs': 1, 'browser': 0}, 'critical': {'fetch': 2, 'parse_workers': 1, 'ml_jobs': 0, 'browser': 0}}[level]
    if concurrency['fetch'] < 4:
        concurrency['fetch'] = 4
    return concurrency
import asyncio
import platform
_CONCURRENCY_FLOOR = 1
_CONCURRENCY_CEILING = 3

def get_adaptive_concurrency() -> int:
    """
    Dynamicky vypočítej optimální concurrency based on memory pressure.
    M1 8GB: max 3, min 1.
    """
    pressure_str = get_memory_pressure_level()
    pressure_map = {'normal': 0.0, 'warn': 0.6, 'critical': 0.9}
    pressure = pressure_map.get(pressure_str, 0.0)
    if pressure < 0.4:
        return _CONCURRENCY_CEILING
    elif pressure < 0.6:
        return 2
    elif pressure < 0.75:
        return 1
    else:
        return _CONCURRENCY_FLOOR

class AdaptiveSemaphore:
    """
    Semaphore whose effective limit adapts to memory pressure.
    Drop-in replacement for asyncio.Semaphore in the orchestrator.

    F130B fix: previous implementation replaced asyncio.Semaphore on limit change,
    orphaning holders — their release() called the new (wrong) semaphore object.
    This version never replaces the semaphore; it enforces the effective limit
    via an active-holder counter, so release() always pairs with the correct object.

    Invariants:
    - Internal semaphore ceiling = _CONCURRENCY_CEILING (3 on M1 8GB).
    - Effective limit is enforced per-acquire via _active_holders counter.
    - When limit drops below active holders, new acquires are rejected immediately.
    - No background cleanup tasks needed.
    """
    _CEILING = _CONCURRENCY_CEILING
    __slots__ = tuple(('_active_holders', '_check_interval', '_effective_limit', '_last_check', '_lock', '_sem'))

    def __init__(self, initial_limit: int=_CONCURRENCY_CEILING) -> Self:
        self._effective_limit = initial_limit
        self._sem = asyncio.Semaphore(self._CEILING)
        self._active_holders = 0
        self._lock = asyncio.Lock()
        self._last_check = 0.0
        self._check_interval = 5.0

    async def _compute_effective_limit(self) -> int:
        """Recompute effective limit if check_interval has elapsed."""
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return self._effective_limit
        self._last_check = now
        self._effective_limit = get_adaptive_concurrency()
        return self._effective_limit

    async def __aenter__(self) -> AdaptiveSemaphore:
        async with self._lock:
            await self._compute_effective_limit()
            if self._active_holders >= self._effective_limit:
                raise RuntimeError(f'AdaptiveSemaphore: concurrency limit ({self._effective_limit}) reached ({self._active_holders} active)')
            self._active_holders += 1
        await self._sem.acquire()
        return self

    async def __aexit__(self, *args) -> None:
        self._sem.release()
        async with self._lock:
            self._active_holders -= 1

    @property
    def current_limit(self) -> int:
        return self._effective_limit

    @property
    def active_holders(self) -> int:
        """For testing / diagnostics only."""
        return self._active_holders

def get_mlx_memory_mb() -> float:
    """
    Vrátí aktuální MLX cache usage v MB.
    Funguje pouze na macOS/Darwin s MLX.
    """
    if platform.system() != 'Darwin':
        return 0.0
    try:
        import mlx.core as mx
        if hasattr(mx.metal, 'get_cache_memory'):
            return mx.get_cache_memory() / (1024 * 1024)
        elif hasattr(mx.metal, 'get_active_memory'):
            return mx.get_active_memory() / (1024 * 1024)
    except Exception:  # noqa: BLE001 — best-effort; MLX memory check failure returns 0.0
        pass
    return 0.0

def clear_mlx_cache_if_needed(threshold_mb: float=500.0) -> bool:
    """
    Uvolni MLX cache pokud přesahuje threshold.
    Vrací True pokud byl cache vyčištěn.
    M1: cache > 500MB = čas uklidit.
    """
    if platform.system() != 'Darwin':
        return False
    try:
        import mlx.core as mx
        cache_mb = get_mlx_memory_mb()
        if cache_mb > threshold_mb:
            mx.eval([])
            import gc
            gc.collect()
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
            elif hasattr(mx.metal, 'clear_cache'):
                mx.metal.clear_cache()
            gc.collect()
            return True
    except Exception:  # noqa: BLE001 — best-effort; MLX cache clear failure is non-fatal
        pass
    return False
from hledac.universal.utils.concurrency import FETCH_SEMAPHORE, adjust_fetch_workers
__all__ = ['FETCH_SEMAPHORE', 'adjust_fetch_workers', 'AdaptiveSemaphore']