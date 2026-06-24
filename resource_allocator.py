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
from typing import Any

# psutil lazy import — only needed inside functions at runtime
_psutil = None


def _get_psutil():
    global _psutil
    if _psutil is not None:
        return _psutil
    try:
        import psutil
        _psutil = psutil
    except Exception:
        _psutil = None
    return _psutil

# Sprint F206AL: Import canonical M1 8GB thresholds from uma_budget.
# MAX_RAM_GB mirrors M1_FETCH_SOFT_CEILING_GB — do not change independently.
# SOFT_PREEMPT_RAM_GIB is intentionally SEPARATE from uma_budget.UMA_EMERGENCY_GIB
# because it governs request-level preemption (not system-level emergency).
from hledac.universal.utils.uma_budget import M1_FETCH_SOFT_CEILING_GB  # noqa: E402

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


class ResourceExhausted(Exception):  # noqa: N818
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
    # Sprint F206AL: MAX_RAM_GB mirrors M1_FETCH_SOFT_CEILING_GB (uma_budget.M1_FETCH_SOFT_CEILING_GB).
    MAX_RAM_GB: float = M1_FETCH_SOFT_CEILING_GB
    # Sprint F206AL: SOFT_PREEMPT_RAM_GIB — request-level soft preemption, NOT system emergency.
    # Value 6.5 equals uma_budget.UMA_CRITICAL_GIB (6.5). The old EMERGENCY_RAM_GB=6.2
    # was incorrectly BELOW uma_budget.UMA_CRITICAL_GIB=6.5, creating a threshold inversion.
    # Correct ordering: WARN(6.0) < SOFT_PREEMPT(6.5) = CRITICAL(6.5) < EMERGENCY(7.0).
    SOFT_PREEMPT_RAM_GIB: float = 6.5
    WARMUP_QUERIES: int = 5

    def __init__(self):
        self.active_requests: dict[str, ResourceBudget] = {}
        self.total_ram_mb: float = 0.0

        # History for MLX linear regression: (features, actual_ram_mb)
        self.history: list[tuple[list[float], float]] = []
        self.coeffs: Any | None = None
        self.warmup_counter: int = 0

    def _extract_features(self, ctx: Any) -> list[float]:
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
            X = mx.array([f for f, _ in self.history])  # noqa: N806
            y = mx.array([a for _, a in self.history])

            # Add bias term (column of ones)
            ones = mx.ones((X.shape[0], 1))
            X = mx.concatenate([X, ones], axis=1)  # noqa: N806

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

    def emergency_brake(self) -> str | None:
        """
        Emergency brake: cancel lowest priority task if RSS > SOFT_PREEMPT_RAM_GIB.
        Returns cancelled request_id or None.
        """
        try:
            _ps = _get_psutil()
            if _ps is None:
                return None
            mem = _ps.virtual_memory()
            if mem.used < self.SOFT_PREEMPT_RAM_GIB * (1024 ** 3):
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

    def get_stats(self) -> dict[str, Any]:
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

# Sprint F265-U: Delegate to AdaptiveWorkerPool (single source of truth for UMA-based scaling)
# Keeping this function for backward compat — maps percent-based levels to AdaptiveWorkerPool states
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
        # Can't await in sync context — return cached state or "normal"
        from utils.concurrency import AdaptiveWorkerPool
        # get_instance is cached, evaluate() will refresh if needed
        pool = AdaptiveWorkerPool._instance
        if pool is not None:
            return _uma_state_to_pressure_level(pool.get_uma_state())
    except Exception:
        pass
    return "normal"


def _uma_state_to_pressure_level(state: str) -> str:
    """Map UMA state string to legacy pressure level string."""
    mapping = {
        "ok": "normal",
        "soft_warn": "normal",
        "warn": "warn",
        "critical": "critical",
        "emergency": "critical",
    }
    return mapping.get(state, "normal")


def get_recommended_concurrency() -> dict[str, int]:
    """
    Return concurrency limits based on memory pressure level.

    Now delegates to AdaptiveWorkerPool (M1ResourceGovernor) for ml_jobs
    and fetch limits. Falls back to legacy behavior if pool unavailable.
    """
    level = get_memory_pressure_level()

    # Try to get adaptive values from AdaptiveWorkerPool
    pool = None
    try:
        from utils.concurrency import AdaptiveWorkerPool
        pool = AdaptiveWorkerPool._instance
    except Exception:
        pass

    if pool is not None and level != "normal":
        # Use AdaptiveWorkerPool's derived values when under pressure
        # (ok/normal state uses legacy defaults for backward compat)
        fetch = pool.get_fetch_limit()
        workers = pool.get_max_workers()
        if level == "critical":
            import gc; gc.collect()  # noqa: E702
        return {
            "fetch": max(4, fetch),  # floor 4 per F221-FIX
            "parse_workers": max(1, workers),
            "ml_jobs": workers,
            "browser": 0 if level == "critical" else 1,
        }

    # Legacy defaults for normal / pool unavailable
    concurrency = {
        "normal":   {"fetch": 20, "parse_workers": 4, "ml_jobs": 1, "browser": 1},
        "warn":     {"fetch": 8,  "parse_workers": 2, "ml_jobs": 1, "browser": 0},
        "critical": {"fetch": 2,  "parse_workers": 1, "ml_jobs": 0, "browser": 0},
    }[level]
    if concurrency["fetch"] < 4:
        concurrency["fetch"] = 4
    return concurrency


# ── Sprint 8VG-C: Adaptive Concurrency ─────────────────────────────────────────

import asyncio  # noqa: E402
import platform  # noqa: E402

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

    async def __aenter__(self) -> AdaptiveSemaphore:
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
            return mx.get_cache_memory() / (1024 * 1024)
        elif hasattr(mx.metal, "get_active_memory"):
            return mx.get_active_memory() / (1024 * 1024)
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
            mx.eval([])  # Flush pending lazy ops before clearing cache (M1 / MLX invariant)
            import gc
            gc.collect()  # F266: Python GC BEFORE Metal release
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
            gc.collect()  # F266: second GC pass
            return True
    except Exception:
        pass
    return False


# ── P3: Dynamic Resource Management ─────────────────────────────────────────

# P19: FETCH_SEMAPHORE moved to utils.concurrency to break circular import
from hledac.universal.utils.concurrency import (  # noqa: E402
    FETCH_SEMAPHORE,
    adjust_fetch_workers,
)

# Re-export for backward compatibility — adjust_fetch_workers already in utils.concurrency
__all__ = ["FETCH_SEMAPHORE", "adjust_fetch_workers", "AdaptiveSemaphore"]

