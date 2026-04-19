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

import time
import psutil
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# MLX is imported lazily inside helpers to avoid paying import tax
# when the predictive model is never used (allocator may only recommend,
# not predict, depending on call site). This keeps the allocator cheap
# when idle on M1 8GB.
MLX_AVAILABLE = False

# Named fallback constant for non-MLX RAM estimation.
# Conservative 500MB default when MLX linear regression is unavailable.
# Chosen because: (a) fits within M1 8GB UMA budget, (b) covers typical
# lightweight research requests, (c) is well above the 100MB minimum floor.
_FALLBACK_RAM_ESTIMATE_MB: float = 500.0


@dataclass
class ResourceBudget:
    """Resource budget for a request."""
    ram_mb: int
    time_sec: float
    priority: int
    request_id: str
    # F130B: context stored so release() can extract features for learning.
    # Without this field, release() has no access to the original ctx,
    # and the MLX linear regression model never learns from actual data.
    context: Any = None


class ResourceExhausted(Exception):
    """Raised when resources cannot be allocated."""
    pass


class ResourceAllocator:
    """
    Predictive resource allocator with:
    - MAX_CONCURRENT: Maximum concurrent requests (default 3)
    - MAX_RAM_GB: Maximum RAM usage before rejecting new requests (default 5.5 GB)
    - EMERGENCY_RAM_GB: Threshold for emergency brake (default 6.2 GB)
    - Warm-up: First 5 queries use fixed allocation
    - MLX-based linear regression for prediction after warm-up
    """

    MAX_CONCURRENT: int = 3
    MAX_RAM_GB: float = 5.5
    EMERGENCY_RAM_GB: float = 6.2
    WARMUP_QUERIES: int = 5

    def __init__(self):
        self.active_requests: Dict[str, ResourceBudget] = {}
        self.total_ram_mb: float = 0.0

        # History for MLX linear regression: (features, actual_ram_mb)
        self.history: List[tuple[List[float], float]] = []
        self.coeffs: Optional[Any] = None
        self.warmup_counter: int = 0

    def _extract_features(self, ctx: Any) -> List[float]:
        """Extract feature vector for RAM prediction."""
        return [
            float(len(ctx.query)) if hasattr(ctx, 'query') else 100.0,
            float(ctx.depth) if hasattr(ctx, 'depth') else 1.0,
            float(len(getattr(ctx, 'selected_sources', []))),
            float(getattr(ctx, 'complexity_score', 0.5)),
        ]

    def _update_model(self):
        """Update MLX linear regression model from history."""
        # F130B: Single warmup gate — model trains once history reaches WARMUP_QUERIES.
        # warmup_counter is incremented in release() when history is empty; kept for
        # compatibility. Train when history is large enough, regardless of counter.
        if len(self.history) < self.WARMUP_QUERIES:
            self.warmup_counter += 1
            return

        try:
            import mlx.core as mx
            # Build feature matrix and target vector
            X = mx.array([f for f, _ in self.history])
            y = mx.array([a for _, a in self.history])

            # Add bias term (column of ones)
            ones = mx.ones((X.shape[0], 1))
            X = mx.concatenate([X, ones], axis=1)

            # Solve least squares: X @ coeffs = y
            self.coeffs, _, _, _ = mx.linalg.lstsq(X, y, rcond=None)
            logger.debug(f"Updated MLX prediction model with {len(self.history)} samples")
        except Exception as e:
            logger.warning(f"Failed to update MLX model: {e}")
            self.coeffs = None

    def predict_ram(self, ctx: Any) -> float:
        """Predict RAM usage for a context using MLX linear regression."""
        if self.coeffs is None:
            # Default prediction during warm-up or if MLX model unavailable
            return _FALLBACK_RAM_ESTIMATE_MB

        try:
            import mlx.core as mx
            features = mx.array(self._extract_features(ctx) + [1.0])  # +1 for bias
            prediction = float(mx.sum(features * self.coeffs))
            return max(100.0, prediction)  # Minimum 100 MB
        except Exception as e:
            logger.warning(f"RAM prediction failed: {e}")
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
            raise ResourceExhausted(f"Cannot accept request {request_id}: resources exhausted")

        predicted = self.predict_ram(ctx)

        budget = ResourceBudget(
            ram_mb=int(predicted),
            time_sec=300.0,
            priority=priority,
            request_id=request_id,
            context=ctx,  # F130B: stored for release() learning path
        )

        self.active_requests[request_id] = budget
        self.total_ram_mb += predicted

        logger.debug(f"Allocated {predicted:.0f} MB for request {request_id} (priority {priority})")

        return budget

    def release(self, request_id: str, actual_ram_mb: float):
        """Release resources and record actual usage for learning."""
        if request_id in self.active_requests:
            budget = self.active_requests.pop(request_id)
            self.total_ram_mb -= budget.ram_mb

            # Record actual usage for MLX learning
            ctx = getattr(budget, 'context', None)
            if ctx is not None:
                features = self._extract_features(ctx)
                self.history.append((features, actual_ram_mb))

                # Keep history bounded
                if len(self.history) > 100:
                    self.history = self.history[-50:]

            self._update_model()
            logger.debug(f"Released request {request_id}, actual RAM: {actual_ram_mb:.0f} MB")

    def emergency_brake(self) -> Optional[str]:
        """
        Emergency brake: cancel lowest priority task if RSS > EMERGENCY_RAM_GB.
        Returns cancelled request_id or None.
        """
        try:
            mem = psutil.virtual_memory()
            if mem.used < self.EMERGENCY_RAM_GB * (1024 ** 3):
                return None

            if not self.active_requests:
                return None

            # Find task with lowest priority (highest priority number = least important)
            lowest = max(
                self.active_requests.values(),
                key=lambda b: b.priority
            )

            self.cancel(lowest.request_id)
            logger.warning(f"Emergency brake: cancelled {lowest.request_id} (RSS: {mem.used / (1024**3):.2f} GB)")
            return lowest.request_id

        except Exception as e:
            logger.error(f"Emergency brake failed: {e}")
            return None

    def cancel(self, request_id: str):
        """Cancel a specific request."""
        if request_id in self.active_requests:
            budget = self.active_requests.pop(request_id)
            self.total_ram_mb -= budget.ram_mb
            logger.info(f"Cancelled request {request_id}")

    def get_stats(self) -> Dict[str, Any]:
        """Get current allocator statistics."""
        return {
            "active_requests": len(self.active_requests),
            "total_ram_mb": self.total_ram_mb,
            "warmup_counter": self.warmup_counter,
            "history_size": len(self.history),
            "model_ready": self.coeffs is not None,
        }


# Sprint 8VD §C: Memory Pressure Governor
# psutil is already imported at the top of this module

def get_memory_pressure_level() -> str:
    """
    Read memory pressure via psutil (ARM64 native, no subprocess overhead).

    Thresholds calibrated for M1 8GB UMA:
      > 85% used  → warn      (~6.8 GB)
      > 93% used  → critical  (~7.4 GB)
    Swap is used as a secondary signal.
    """
    try:
        vm = psutil.virtual_memory()
        pct = vm.percent
        sw = psutil.swap_memory()
        if pct > 93 or sw.percent > 50:
            return "critical"
        if pct > 85 or sw.percent > 25:
            return "warn"
    except Exception:
        pass
    return "normal"


def get_recommended_concurrency() -> Dict[str, int]:
    """
    Return concurrency limits based on memory pressure level.

    AUTHORITY BOUNDARY: This function returns RECOMMENDATIONS only.
    It does NOT perform model-plane operations (e.g. ANE unload).
    Callers in the model plane are responsible for acting on critical-level
    recommendations and performing any required unload/cleanup.

    Memory pressure thresholds (percent-based, independent of uma_budget.py MB thresholds):
      normal   → standard concurrency
      warn     → reduced concurrency
      critical → minimal concurrency; caller should consider model-plane cleanup
    """
    level = get_memory_pressure_level()
    if level == "critical":
        import gc; gc.collect()
    return {
        "normal":   {"fetch": 20, "parse_workers": 4, "ml_jobs": 1, "browser": 1},
        "warn":     {"fetch": 8,  "parse_workers": 2, "ml_jobs": 0, "browser": 0},
        "critical": {"fetch": 2,  "parse_workers": 1, "ml_jobs": 0, "browser": 0},
    }[level]


# ── Sprint 8VG-C: Adaptive Concurrency ─────────────────────────────────────────

import asyncio
import platform
import time

_CONCURRENCY_FLOOR = 1
_CONCURRENCY_CEILING = 3  # M1 8GB hard limit


def get_adaptive_concurrency() -> int:
    """
    Dynamicky vypočítej optimální concurrency based on memory pressure.
    M1 8GB: max 3, min 1.
    """
    pressure_str = get_memory_pressure_level()
    # Map string level to numeric 0-1 range
    pressure_map = {"normal": 0.0, "warn": 0.6, "critical": 0.9}
    pressure = pressure_map.get(pressure_str, 0.0)

    if pressure < 0.4:
        return _CONCURRENCY_CEILING      # 3 paralelní tasks
    elif pressure < 0.6:
        return 2                          # 2 tasks
    elif pressure < 0.75:
        return 1                          # 1 task — opatrně
    else:
        return _CONCURRENCY_FLOOR         # memory critical — force sequential


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

    def __init__(self, initial_limit: int = _CONCURRENCY_CEILING):
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

    async def __aenter__(self) -> "AdaptiveSemaphore":
        async with self._lock:
            await self._compute_effective_limit()
            if self._active_holders >= self._effective_limit:
                raise RuntimeError(
                    f"AdaptiveSemaphore: concurrency limit ({self._effective_limit}) "
                    f"reached ({self._active_holders} active)"
                )
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
    if platform.system() != "Darwin":
        return 0.0
    try:
        import mlx.core as mx
        if hasattr(mx.metal, "get_cache_memory"):
            return mx.metal.get_cache_memory() / (1024 * 1024)
        elif hasattr(mx.metal, "get_active_memory"):
            return mx.metal.get_active_memory() / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def clear_mlx_cache_if_needed(threshold_mb: float = 500.0) -> bool:
    """
    Uvolni MLX cache pokud přesahuje threshold.
    Vrací True pokud byl cache vyčištěn.
    M1: cache > 500MB = čas uklidit.
    """
    if platform.system() != "Darwin":
        return False
    try:
        import mlx.core as mx
        cache_mb = get_mlx_memory_mb()
        if cache_mb > threshold_mb:
            if hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
                return True
    except Exception:
        pass
    return False


# ── P3: Dynamic Resource Management ─────────────────────────────────────────

FETCH_SEMAPHORE = asyncio.Semaphore(25)


async def adjust_fetch_workers(new_limit: int) -> None:
    """
    Dynamicky uprav limit FETCH_SEMAPHORE.

    Použití:
    - Po načtení LLM: await adjust_fetch_workers(3)  # snížit concurrency
    - Po uvolnění LLM: await adjust_fetch_workers(25)  # obnovit plnou concurrency

    Args:
        new_limit: Nový počet povolených paralelních fetchů
    """
    global FETCH_SEMAPHORE
    old_limit = FETCH_SEMAPHORE._value
    FETCH_SEMAPHORE = asyncio.Semaphore(max(1, new_limit))
    logger.info(f"[FETCH_WORKERS] Adjusted from {old_limit} to {new_limit}")

