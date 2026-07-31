"""
coordinators/resource/resource_coordinator.py — Unified Resource Management
========================================================================

Consolidated resource management layer for M1 8GB:

GC STRATEGY (from gc_policy.py):
    - Centralized gc.collect() in hot paths via asyncio.to_thread()
    - mx.eval([]) before gc.collect() — clear_cache is no-op without barrier
    - Bounded: GC_GEN0=700, GC_GEN1=50, GC_GEN2=20

BACKPRESSURE (from backpressure.py):
    - Memory-pressure-driven concurrency governor for fetch pipeline
    - Translates GovernorDecision into fetch AIMD limits
    - Evaluates lazily with TTL cache

AIMD WINDOWING (from aimd_controllers.py):
    - Unified AIMD controller for fetch/enrichment/extraction stages
    - Thread-safe under asyncio.Lock
    - Bounded: min/max clamps

M1 RESOURCE COORDINATION (from resource_allocator.py):
    - Simplified M1ResourceCoordinator (sklearn removed from hot path)
    - CapacitySnapshot with TTL caching
    - can_use_ane(), get_recommended_concurrency()

M1 8GB INVARIANTS:
    - Always-on, no feature flags
    - mx.eval([]) before gc.collect() — F266-U4
    - gc.freeze() for pinned objects — F266-U4
    - Bounded: MAX_CONCURRENT_FETCH=20, MAX_ENRICHMENT=16, MAX_EXTRACTION=8
    - asyncio.gather with return_exceptions=True
"""

from __future__ import annotations

import asyncio
import gc as _gc
import logging
import subprocess
import sys
import threading
import time as _time_module
from collections import deque
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import msgspec
from msgspec import field

if TYPE_CHECKING:
    from hledac.universal.core.resource_governor import GovernorDecision


@runtime_checkable
class GovernorProtocol(Protocol):
    """Duck-typed seam: GovernorDecision provider for BackpressureMonitor."""

    async def evaluate(self) -> GovernorDecision: ...

logger = logging.getLogger(__name__)

# =============================================================================
# GC STRATEGY
# =============================================================================

# M1 8GB thresholds — agresivnější gen-0, šetrnější gen-2
_GC_THRESHOLD = (700, 50, 20)
_GC_FREEZE_ENABLED: bool = sys.version_info >= (3, 14, 7)
_configured = False
_configure_lock = threading.Lock()


def _ensure_configured() -> None:
    """Apply gc.set_threshold and gc.freeze() — called once at startup."""
    global _configured
    if _configured:
        return
    with _configure_lock:
        if _configured:  # type: ignore[unreachable] # false positive: reachable when another thread set _configured
            return
        _apply_gc_config()


def _apply_gc_config() -> None:
    """Apply gc thresholds + freeze. Idempotent."""
    try:
        _gc.set_threshold(*_GC_THRESHOLD)
        logger.debug(f"[GC] set_threshold{_GC_THRESHOLD}")
    except Exception as exc:
        logger.debug(f"[GC] set_threshold failed: {exc}")
    if _GC_FREEZE_ENABLED:
        try:
            _gc.freeze()
            logger.debug("[GC] freeze() applied at startup")
        except Exception as exc:
            logger.debug(f"[GC] freeze failed: {exc}")
    global _configured
    _configured = True


def gc_collect(generation: Literal[0, 1, 2] = 0) -> None:
    """
    Fail-safe gc.collect() — called from thread pool via asyncio.to_thread().

    Args:
        generation: 0 = gen-0 only (fast), 2 = full sweep (expensive)
    """
    try:
        _gc.collect(generation)
    except Exception as exc:
        logger.debug(f"[GC] gc.collect({generation}) failed: {exc}")


def gc_collect_aggressive() -> None:
    """
    Agresivní GC: gen-0 + freeze. Pro winddown fázi sprintu.
    Canonical order: gc.collect(0) → mx.eval([]) → gc.freeze()
    """
    try:
        _gc.collect(0)
    except Exception as exc:
        logger.debug(f"[GC] gc.collect(0) failed: {exc}")
    if _GC_FREEZE_ENABLED:
        try:
            _gc.freeze()
        except Exception as exc:
            logger.debug(f"[GC] freeze failed: {exc}")


async def gc_collect_async(
    generation: Literal[0, 1, 2] = 0,
    force_aggressive: bool = False,
) -> None:
    """
    Async wrapper — gc.collect() in thread pool, does not block event loop.

    Args:
        generation: 0 = gen-0 only (fast), 2 = full sweep (expensive)
        force_aggressive: if True, also runs gen-2 + freeze
    """
    _ensure_configured()

    def _work() -> None:
        if force_aggressive:
            gc_collect_aggressive()
        else:
            gc_collect(generation)

    try:
        await asyncio.to_thread(_work)
    except Exception as exc:
        logger.debug(f"[GC] async gc_collect failed: {exc}")


def get_gc_stats() -> dict[str, Any]:
    """Return GC stats for telemetry."""
    try:
        stats = _gc.get_stats()
        return {
            "generation_thresholds": _GC_THRESHOLD,
            "gc_freeze_enabled": _GC_FREEZE_ENABLED,
            "generation_stats": stats if stats else [],
        }
    except Exception as exc:
        logger.debug(f"[GC] get_stats failed: {exc}")
        return {}


# =============================================================================
# AIMD CONTROLLER
# =============================================================================

# Fetch AIMD constants
AIMD_FETCH_ADDITIVE_INCREMENT = 2
AIMD_FETCH_DECREASE_FACTOR = 0.75
AIMD_FETCH_MIN = 1
AIMD_FETCH_MAX = 25
AIMD_FETCH_SUCCESS_THRESHOLD = 2

# Enrichment AIMD constants
AIMD_ENRICHMENT_ADDITIVE_INCREMENT = 1
AIMD_ENRICHMENT_DECREASE_FACTOR = 0.75
AIMD_ENRICHMENT_MIN = 1
AIMD_ENRICHMENT_MAX = 16

# Extraction AIMD constants
AIMD_EXTRACTION_ADDITIVE_INCREMENT = 1
AIMD_EXTRACTION_DECREASE_FACTOR = 0.75
AIMD_EXTRACTION_MIN = 1
AIMD_EXTRACTION_MAX = 8


class AIMDController(msgspec.Struct, gc=False):
    """
    Unified AIMD (Additive Increase/Multiplicative Decrease) controller.

    Thread-safe: all mutations under asyncio.Lock.

    Used for:
        - Fetch concurrency (fetch_coordinator.py)
        - Enrichment workers (P2-3 stage)
        - Extraction workers (P2-3 stage)

    M1 8GB bounds:
        - fetch: [1, 25] with 3s adaptive window
        - enrichment: [1, 16] CPU-bound ceiling
        - extraction: [1, 8] I/O-bound ceiling

    Usage:
        controller = AIMDController(
            min_value=1,
            max_value=16,
            additive_increment=1,
            decrease_factor=0.75,
            success_threshold=2,
            name="enrich",
        )
        window = await controller.on_success()  # increase
        window = await controller.on_failure()  # decrease
    """
    min_value: float
    max_value: float
    additive_increment: float
    decrease_factor: float
    success_threshold: int
    name: str

    _window: float = 1.0
    _successes: int = 0
    _failures: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _stats: dict[str, int] = field(
        default_factory=lambda: {
            "increases": 0,
            "decreases": 0,
            "window_changes": 0,
            "successes": 0,
            "failures": 0,
        },
    )

    def __post_init__(self) -> None:
        self._window = min(self.max_value, max(self.min_value, self.additive_increment * 2))
        self._successes = 0
        self._failures = 0
        self._lock = asyncio.Lock()

    @property
    def window(self) -> float:
        return self._window

    @property
    def successes(self) -> int:
        return self._successes

    @property
    def failures(self) -> int:
        return self._failures

    async def on_success(self) -> float:
        """Increase window after success."""
        async with self._lock:
            self._successes += 1
            self._stats["successes"] += 1
            if self._successes >= self.success_threshold:
                new_window = min(self.max_value, self._window + self.additive_increment)
                if new_window != self._window:
                    self._window = new_window
                    self._stats["increases"] += 1
                    self._stats["window_changes"] += 1
                self._successes = 0
            return self._window

    async def on_failure(self) -> float:
        """Decrease window after failure."""
        async with self._lock:
            self._failures += 1
            self._stats["failures"] += 1
            new_window = max(self.min_value, self._window * self.decrease_factor)
            if new_window != self._window:
                self._window = new_window
                self._stats["decreases"] += 1
                self._stats["window_changes"] += 1
            self._successes = 0
            return self._window

    async def reset(self) -> None:
        """Reset window to initial value."""
        async with self._lock:
            self._window = min(self.max_value, max(self.min_value, self.additive_increment * 2))
            self._successes = 0
            self._failures = 0

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "window": self._window,
            "successes": self._successes,
            "failures": self._failures,
            "name": self.name,
        }


def make_fetch_aimd() -> AIMDController:
    """Factory for FetchStage AIMD controller — ceiling=25, fast scaling."""
    return AIMDController(
        min_value=AIMD_FETCH_MIN,
        max_value=AIMD_FETCH_MAX,
        additive_increment=AIMD_FETCH_ADDITIVE_INCREMENT,
        decrease_factor=AIMD_FETCH_DECREASE_FACTOR,
        success_threshold=AIMD_FETCH_SUCCESS_THRESHOLD,
        name="fetch",
    )


def make_extract_aimd() -> AIMDController:
    """Factory for extraction AIMD controller — ceiling=8, conservative scaling."""
    return AIMDController(
        min_value=AIMD_EXTRACTION_MIN,
        max_value=AIMD_EXTRACTION_MAX,
        additive_increment=AIMD_EXTRACTION_ADDITIVE_INCREMENT,
        decrease_factor=AIMD_EXTRACTION_DECREASE_FACTOR,
        success_threshold=2,
        name="extract",
    )


# =============================================================================
# BACKPRESSURE MONITOR
# =============================================================================

_DEFAULT_CLEARNET_MAX = 5
_DEFAULT_STEALTH_MAX = 3
_MIN_CLEARNET = 1
_MAX_CLEARNET_FROM_GOVERNOR = 20


class BackpressureDecision(msgspec.Struct, frozen=True, gc=False):
    """
    Backpressure decision for the fetch lane.
    Derived from GovernorDecision but scoped to fetch concurrency only.

    HW-03: Includes thermal scaling factors for worker/batch adjustment.
    """
    clearnet_max: int
    stealth_max: int
    uma_state: str
    io_only: bool
    swap_detected: bool = False  # ISSUE-35: expose swap signal for telemetry
    # HW-03: Thermal scaling factors from GovernorDecision
    thermal_throttled: bool = False
    thermal_headroom: float = 1.0
    worker_scale_factor: float = 1.0  # 0.0-1.0, scales max_workers
    batch_scale_factor: float = 1.0  # 0.0-1.0, scales MLX batch size


class BackpressureMonitor:
    """
    Memory-pressure monitor that translates GovernorDecision into fetch lane limits.

    Lives in the scheduler; called by FetchCoordinator on every _aimd_acquire().
    The provider callable is the seam — FetchCoordinator never imports this module directly.
    """
    __slots__ = (
        "_decision", "_governor", "_last_evaluate", "_last_state",
        "_lock", "_max_clearnet", "_min_clearnet", "_state_changes",
    )

    def __init__(
        self,
        governor: GovernorProtocol,
        min_clearnet: int = _MIN_CLEARNET,
        max_clearnet: int = _DEFAULT_CLEARNET_MAX,
    ) -> None:
        self._governor = governor
        self._min_clearnet = min_clearnet
        self._max_clearnet = max_clearnet
        self._decision: BackpressureDecision = BackpressureDecision(
            clearnet_max=max_clearnet,
            stealth_max=_DEFAULT_STEALTH_MAX,
            uma_state="ok",
            io_only=False,
        )
        self._last_evaluate: float = 0.0
        self._lock = asyncio.Lock()
        self._state_changes: int = 0
        self._last_state: str = "ok"

    async def evaluate(self) -> BackpressureDecision:
        """
        Re-evaluate backpressure from GovernorDecision.
        Caches result; subsequent calls within TTL return cached value.
        TTL sourced from ConcurrencyPreset.cache_ttl_seconds (SSOT).
        """
        now = _time_module.monotonic()
        try:
            from hledac.universal.core.resource_governor import ConcurrencyPreset
            cache_ttl = ConcurrencyPreset.from_state(self._decision.uma_state).cache_ttl_seconds
        except Exception:
            cache_ttl = 5.0  # safe default

        if now - self._last_evaluate < cache_ttl:
            return self._decision

        async with self._lock:
            if now - self._last_evaluate < cache_ttl:
                return self._decision
            try:
                governor_decision = await self._governor.evaluate()
            except Exception:
                self._decision = BackpressureDecision(
                    clearnet_max=self._max_clearnet,
                    stealth_max=_DEFAULT_STEALTH_MAX,
                    uma_state="ok",
                    io_only=False,
                    swap_detected=False,
                )
                self._last_evaluate = now
                return self._decision

            governor_cap = governor_decision.fetch_limit
            # HW-03: Apply thermal scaling to fetch concurrency
            worker_scale = governor_decision.worker_scale_factor
            scaled_cap = max(1, int(governor_cap * worker_scale))
            clearnet_max = max(self._min_clearnet, min(scaled_cap, self._max_clearnet))
            stealth_max = max(1, clearnet_max - 1)
            # ISSUE-35: Use governor's io_only directly — it already incorporates
            # swap_detected signal and hysteresis via should_enter_io_only_mode().
            io_only = governor_decision.io_only
            new_decision = BackpressureDecision(
                clearnet_max=clearnet_max,
                stealth_max=stealth_max,
                uma_state=governor_decision.uma_state,
                io_only=io_only,
                swap_detected=governor_decision.swap_detected,
                thermal_throttled=governor_decision.thermal_throttled,
                thermal_headroom=governor_decision.thermal_headroom,
                worker_scale_factor=governor_decision.worker_scale_factor,
                batch_scale_factor=governor_decision.batch_scale_factor,
            )
            if new_decision.uma_state != self._last_state:
                # F1 FIX: propagate UMA state to ConcurrencyBudgetRegistry so all
                # parallel() call sites globally respect the same memory pressure limits.
                try:
                    from hledac.universal.core.concurrency_registry import ConcurrencyBudgetRegistry
                    registry = await ConcurrencyBudgetRegistry.get_instance_async()
                    await registry.adjust_for_state(new_decision.uma_state)
                except Exception:
                    pass  # fail-safe: registry errors never block backpressure decision
                logger.info(
                    f"[BACKPRESSURE] uma_state: {self._last_state} → "
                    f"{new_decision.uma_state} "
                    f"(clearnet_max={clearnet_max}, stealth_max={stealth_max})"
                )
                self._state_changes += 1
                self._last_state = new_decision.uma_state
            self._decision = new_decision
            self._last_evaluate = now
            return self._decision

    def get_decision(self) -> BackpressureDecision:
        """
        Synchronous read of cached decision.
        Returns safe defaults if never evaluated.
        """
        return self._decision

    def backpressure_provider(self) -> tuple[int, int, str, bool]:
        """
        Returns (clearnet_max, stealth_max, uma_state, io_only).
        Callable signature — no async, no self consumption.
        Used as `concurrency_provider` kwarg to FetchCoordinator.
        """
        d = self._decision
        return (d.clearnet_max, d.stealth_max, d.uma_state, d.io_only)

    def get_telemetry(self) -> dict[str, Any]:
        """For diagnostics and dashboard."""
        return {
            "clearnet_max": self._decision.clearnet_max,
            "stealth_max": self._decision.stealth_max,
            "uma_state": self._decision.uma_state,
            "io_only": self._decision.io_only,
            "swap_detected": self._decision.swap_detected,
            "state_changes": self._state_changes,
            # HW-03: Thermal telemetry
            "thermal_throttled": self._decision.thermal_throttled,
            "thermal_headroom": self._decision.thermal_headroom,
            "worker_scale_factor": self._decision.worker_scale_factor,
            "batch_scale_factor": self._decision.batch_scale_factor,
        }


# =============================================================================
# M1 RESOURCE COORDINATOR
# =============================================================================

MAX_PENDING_RESOURCE_REQUESTS = 1000


class CapacitySnapshot(msgspec.Struct, frozen=True, gc=False):
    """Immutable snapshot of M1 resource capacity with TTL tracking."""
    cpu_percent: float
    gpu_memory: float
    gpu_usage: float
    metal_available: bool
    sampled_at_monotonic: float


class _CapacitySampler:
    """
    Async-owned resource capacity sampler with TTL caching.

    Offloads blocking psutil.cpu_percent(interval=1) and system_profiler
    calls from async hot paths via asyncio.to_thread.
    """
    _CPU_TTL_S = 3.0
    _METAL_TTL_S = 300.0

    __slots__ = (
        "_cpu_cache", "_cpu_lock",
        "_metal_cache", "_metal_cache_time", "_metal_lock",
    )

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
        """
        from hledac.universal.core.psutil_shim import psutil as _ps

        assert _ps is not None
        ps = _ps
        cpu_percent = ps.cpu_percent(interval=0.0)
        ps.virtual_memory()
        gpu_memory = 0.0
        gpu_usage = cpu_percent * 0.7
        return (cpu_percent, gpu_memory, gpu_usage)

    def _get_metal_sync(self) -> bool:
        """
        Blocking system_profiler call for Metal availability.
        MUST be called via asyncio.to_thread.
        """
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return "Metal" in result.stdout
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return False

    async def sample(self) -> CapacitySnapshot:
        """
        Get capacity snapshot with per-field TTL caching.
        CPU/performance metrics: short TTL (3s).
        Metal availability: long TTL (300s).
        """
        now = _time_module.monotonic()
        cached = self._cpu_cache
        if cached is not None and now - cached.sampled_at_monotonic < self._CPU_TTL_S:
            return cached
        async with self._cpu_lock:
            now = _time_module.monotonic()
            if self._cpu_cache is not None and now - self._cpu_cache.sampled_at_monotonic < self._CPU_TTL_S:
                return self._cpu_cache
            cpu_percent, gpu_memory, gpu_usage = await asyncio.to_thread(self._get_cpu_sync)
            metal_available = await self._get_metal_with_cache(now)
            self._cpu_cache = CapacitySnapshot(
                cpu_percent=cpu_percent,
                gpu_memory=gpu_memory,
                gpu_usage=gpu_usage,
                metal_available=metal_available,
                sampled_at_monotonic=now,
            )
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


class M1ResourceCoordinator:
    """
    Simplified M1 resource coordinator.

    Responsibilities:
        - Capacity snapshot sampling (CPU, Metal availability)
        - ANE suitability check
        - Concurrency recommendations per task type
        - GC strategy coordination (via gc_collect_async)

    NOT responsible for:
        - Task scheduling (ResourceAwareScheduler — separate concern)
        - Fetch concurrency (BackpressureMonitor — separate concern)
        - MLX model loading (brain/ layer)

    M1 8GB bounds:
        - MAX_CONCURRENT_FETCH = 20
        - MAX_ENRICHMENT_WORKERS = 16
        - MAX_EXTRACTION_WORKERS = 8
    """
    __slots__ = (
        "_capacity_sampler",
        "completed_allocations",
        "active_allocations",
        "m1_optimizations",
    )

    def __init__(self) -> None:
        self._capacity_sampler = _CapacitySampler()
        self.active_allocations: dict[str, dict[str, Any]] = {}
        self.completed_allocations = deque(maxlen=2000)
        self.m1_optimizations = {
            "cpu_efficiency_cores": 4,
            "cpu_performance_cores": 4,
            "memory_bandwidth": 68.25,
            "unified_memory": True,
            "neural_engine": True,
        }

    async def sample_capacity(self) -> CapacitySnapshot:
        """Get current M1 capacity snapshot."""
        return await self._capacity_sampler.sample()

    async def can_use_ane(self) -> bool:
        """
        Decide whether ANE embedder is suitable given current load.

        Returns:
            True if ANE embedder should be used
        """
        try:
            from hledac.universal.brain.ane_embedder import ANE_AVAILABLE
        except ImportError:
            return False
        if not ANE_AVAILABLE:
            return False
        capacity = await self._capacity_sampler.sample()
        return capacity.gpu_usage < 0.7

    async def get_recommended_concurrency(self, task_type: str) -> int:
        """
        Return recommended concurrency by task type and current memory pressure.

        Args:
            task_type: 'io' or 'cpu'

        M1 8GB bounds:
            - io: base=10, clamped by memory
            - cpu: base=4, clamped by memory
        """
        try:
            from hledac.universal.core.psutil_shim import psutil as _psutil

            assert _psutil is not None
            psutil = _psutil
        except (ImportError, AssertionError):
            return 10 if task_type == "io" else 4

        mem = psutil.virtual_memory()
        base = 10 if task_type == "io" else 4

        if mem.percent > 75:
            return max(1, base // 4)
        elif mem.percent > 60:
            return max(1, base // 2)
        return base

    async def gc_collect(
        self,
        generation: Literal[0, 1, 2] = 0,
        force_aggressive: bool = False,
    ) -> None:
        """
        Orchestrate GC collection.

        Args:
            generation: 0 = gen-0 only (fast), 2 = full sweep
            force_aggressive: if True, also freeze
        """
        await gc_collect_async(generation=generation, force_aggressive=force_aggressive)

    def get_stats(self) -> dict[str, Any]:
        """Return resource stats for telemetry."""
        try:
            from hledac.universal.core.psutil_shim import psutil as _ps

            assert _ps is not None
            ps = _ps
            mem = ps.virtual_memory()
            return {
                "active_allocations": len(self.active_allocations),
                "completed_allocations": len(self.completed_allocations),
                "memory_percent": mem.percent,
                "m1_optimizations": self.m1_optimizations,
            }
        except Exception:
            return {
                "active_allocations": len(self.active_allocations),
                "completed_allocations": len(self.completed_allocations),
            }


__all__ = [
    # GC
    "gc_collect",
    "gc_collect_aggressive",
    "gc_collect_async",
    "get_gc_stats",
    # Backpressure
    "BackpressureDecision",
    "BackpressureMonitor",
    # AIMD
    "AIMDController",
    "make_fetch_aimd",
    "make_extract_aimd",
    # M1 Resource
    "CapacitySnapshot",
    "M1ResourceCoordinator",
]
