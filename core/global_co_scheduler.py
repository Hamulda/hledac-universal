# GlobalPeakCoScheduler — Unified asyncio.TaskGroup-based co-scheduler for M1 8GB UMA.
# UNIFIED-003: Peak-Load Global Co-Scheduler & Mutual Exclusion
#
# This module provides the TOP-LEVEL orchestration layer that coordinates
# ALL memory-intensive subsystems through a single admission pipeline.
#
# ARCHITECTURE:
#     GlobalPeakCoScheduler
#         ├── GlobalPeakLoadCoordinator (admission control + mutex + UMA)
#         ├── M1ResourceGovernor (pressure states + hysteresis)
#         └── asyncio.TaskGroup (structured concurrency, Python 3.11+)
#
# KEY INVARIANT:
#     No subsystem may allocate > 100 MB peak memory without going through
#     the co-scheduler. This is enforced by SubsystemRegistry — every
#     memory-intensive subsystem MUST register at startup.
#
# USAGE:
#     scheduler = get_co_scheduler()
#     async with scheduler.guard(Subsystem.MLX, estimated_mb=2500) as ctx:
#         await run_mlx_inference()
#
# FAIL-SOFT:
#     If the co-scheduler is unavailable (not initialized), subsystems
#     fall back to their existing local admission path (fail-open).
#
# M1 8GB CALIBRATION:
#     Total budget: 6.48 GiB (81% of 8 GB)
#     Operating range: 5.5 - 6.0 GiB for workload
#     Hard stop at:   6.29 GiB (97% emergency cutoff)

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import msgspec

logger = logging.getLogger(__name__)

# =============================================================================
# ENUMS
# =============================================================================


class Subsystem(StrEnum):
    """Registered subsystems that require admission control.

    Each subsystem declares its memory profile and hardware constraints.
    UNIFIED-003: This enum is the canonical registry — no subsystem may
    bypass the co-scheduler for allocations > 100 MB.
    """

    MLX_INFERENCE = "mlx_inference"          # Hermes3 LLM ~2.5 GB
    ANE_VISION_OCR = "ane_vision_ocr"        # Vision Framework OCR ~1.5 GB
    TANTIVY_INDEX = "tantivy_index"          # Rust mmap fulltext ~2.0 GB
    NETWORK_FETCH = "network_fetch"          # curl_cffi/httpx bulk ~0.8 GB
    SIDECAR_ADVISORY = "sidecar_advisory"    # IPFS/Tor/I2P/BGP ~0.1 GB each
    RAG_EMBEDDING = "rag_embedding"          # MLX embeddings ~0.4 GB
    METAL_HNSW = "metal_hnsw"                # GPU HNSW construction ~0.5 GB
    WHISPER_STT = "whisper_stt"              # CoreML/ANE STT ~0.3 GB
    DUCKDB_QUERY = "duckdb_query"            # Large analytical queries ~0.5 GB
    LMDB_MMAP = "lmdb_mmap"                  # LMDB bulk mmap ~0.3 GB


# Maps Subsystem -> ResourceClass in peak_load_coordinator
_SUBSYSTEM_TO_RESOURCE: dict[Subsystem, str] = {
    Subsystem.MLX_INFERENCE: "mlx_generation",
    Subsystem.ANE_VISION_OCR: "ane_vision",
    Subsystem.TANTIVY_INDEX: "tantivy_index",
    Subsystem.NETWORK_FETCH: "network_fetch",
    Subsystem.SIDECAR_ADVISORY: "sidecar_advisory",
    Subsystem.RAG_EMBEDDING: "mlx_generation",      # Shares Metal with MLX
    Subsystem.METAL_HNSW: "mlx_generation",          # Shares Metal with MLX
    Subsystem.WHISPER_STT: "ane_vision",              # Shares ANE with Vision
    Subsystem.DUCKDB_QUERY: "network_fetch",          # I/O-bound, low priority
    Subsystem.LMDB_MMAP: "network_fetch",             # I/O-bound, low priority
}


class CoSchedulerState(StrEnum):
    """Operational state of the co-scheduler."""

    UNINITIALIZED = "uninitialized"
    IDLE = "idle"
    ACTIVE = "active"
    DEGRADED = "degraded"    # Operating with reduced budget
    SHUTDOWN = "shutdown"


# =============================================================================
# SUBSYSTEM PROFILE
# =============================================================================


class SubsystemProfile(msgspec.Struct, frozen=True, gc=False):
    """Declared resource profile for a subsystem.

    Each subsystem declares its estimated peak memory, default priority,
    and timeout tolerance. This profile is used by the co-scheduler to
    make admission decisions without needing per-call configuration.
    """

    subsystem: Subsystem
    estimated_mb: float
    default_priority: str = "normal"  # "critical" | "high" | "normal" | "low"
    timeout_s: float = 30.0
    max_concurrent: int = 1  # Max concurrent instances of this subsystem
    preemptible: bool = True  # Can be cancelled during memory pressure
    description: str = ""


# Default profiles — M1 8GB calibrated
_DEFAULT_PROFILES: dict[Subsystem, SubsystemProfile] = {
    Subsystem.MLX_INFERENCE: SubsystemProfile(
        subsystem=Subsystem.MLX_INFERENCE,
        estimated_mb=2500.0,
        default_priority="high",
        timeout_s=10.0,
        max_concurrent=1,
        preemptible=False,  # MLX cleanup is expensive, prefer not to preempt
        description="Hermes3 3B 4-bit MLX inference (~2.5 GB peak)",
    ),
    Subsystem.ANE_VISION_OCR: SubsystemProfile(
        subsystem=Subsystem.ANE_VISION_OCR,
        estimated_mb=1500.0,
        default_priority="normal",
        timeout_s=5.0,
        max_concurrent=1,
        preemptible=True,
        description="Vision Framework ANE OCR (~1.5 GB peak)",
    ),
    Subsystem.TANTIVY_INDEX: SubsystemProfile(
        subsystem=Subsystem.TANTIVY_INDEX,
        estimated_mb=2000.0,
        default_priority="normal",
        timeout_s=10.0,
        max_concurrent=1,
        preemptible=True,
        description="Tantivy mmap fulltext indexing (~2.0 GB peak)",
    ),
    Subsystem.NETWORK_FETCH: SubsystemProfile(
        subsystem=Subsystem.NETWORK_FETCH,
        estimated_mb=800.0,
        default_priority="normal",
        timeout_s=5.0,
        max_concurrent=4,
        preemptible=True,
        description="Bulk HTTP fetching (~0.8 GB peak for 4 concurrent)",
    ),
    Subsystem.SIDECAR_ADVISORY: SubsystemProfile(
        subsystem=Subsystem.SIDECAR_ADVISORY,
        estimated_mb=120.0,
        default_priority="low",
        timeout_s=5.0,
        max_concurrent=8,
        preemptible=True,
        description="Advisory sidecar pool (~15 MB each, max 8 concurrent)",
    ),
    Subsystem.RAG_EMBEDDING: SubsystemProfile(
        subsystem=Subsystem.RAG_EMBEDDING,
        estimated_mb=400.0,
        default_priority="normal",
        timeout_s=8.0,
        max_concurrent=1,
        preemptible=True,
        description="MLX embedding generation (~0.4 GB peak)",
    ),
    Subsystem.METAL_HNSW: SubsystemProfile(
        subsystem=Subsystem.METAL_HNSW,
        estimated_mb=500.0,
        default_priority="low",
        timeout_s=15.0,
        max_concurrent=1,
        preemptible=True,
        description="Metal GPU HNSW index construction (~0.5 GB peak)",
    ),
    Subsystem.WHISPER_STT: SubsystemProfile(
        subsystem=Subsystem.WHISPER_STT,
        estimated_mb=300.0,
        default_priority="low",
        timeout_s=10.0,
        max_concurrent=1,
        preemptible=True,
        description="Whisper.cpp CoreML speech-to-text (~0.3 GB peak)",
    ),
    Subsystem.DUCKDB_QUERY: SubsystemProfile(
        subsystem=Subsystem.DUCKDB_QUERY,
        estimated_mb=500.0,
        default_priority="normal",
        timeout_s=20.0,
        max_concurrent=2,
        preemptible=True,
        description="DuckDB analytical query (~0.5 GB peak)",
    ),
    Subsystem.LMDB_MMAP: SubsystemProfile(
        subsystem=Subsystem.LMDB_MMAP,
        estimated_mb=300.0,
        default_priority="low",
        timeout_s=5.0,
        max_concurrent=2,
        preemptible=True,
        description="LMDB bulk mmap operations (~0.3 GB peak)",
    ),
}


# =============================================================================
# ADMISSION CONTEXT (returned by guard())
# =============================================================================


class AdmissionContext(msgspec.Struct, frozen=True, gc=False):
    """Diagnostic context returned after admission is granted."""

    subsystem: str
    allocated_mb: float
    wait_time_s: float
    peak_utilization: float  # 0.0-1.0
    mutex_held: str | None   # MutexGroup held, if any


# =============================================================================
# CO-SCHEDULER TELEMETRY
# =============================================================================


@dataclass
class CoSchedulerTelemetry:
    """Aggregate telemetry for the co-scheduler."""

    state: CoSchedulerState = CoSchedulerState.UNINITIALIZED
    total_admissions: int = 0
    total_rejections: int = 0
    total_preemptions: int = 0
    total_uma_rejections: int = 0
    total_timeouts: int = 0
    active_guards: int = 0
    peak_concurrent_mb: float = 0.0
    last_snapshot_time: float = 0.0


# =============================================================================
# GLOBAL PEAK CO-SCHEDULER
# =============================================================================


class GlobalPeakCoScheduler:
    """Unified admission scheduler for all memory-intensive subsystems.

    Wraps GlobalPeakLoadCoordinator with:
      - Subsystem registration + profile-based defaults
      - Python 3.11+ asyncio.TaskGroup for structured concurrency
      - Telemetry aggregation across all subsystems
      - Sprint deadline integration
      - Graceful degradation (fail-open when unavailable)

    SINGLETON: use get_co_scheduler() to access.
    """

    __slots__ = (
        "_lock",
        "_lock_factory",
        "_state",
        "_coordinator",
        "_profiles",
        "_active_guards",
        "_telemetry",
        "_subsystem_semaphores",
    )

    def __init__(self) -> None:
        self._lock_factory = threading.Lock()
        self._lock: asyncio.Lock | None = None
        self._state = CoSchedulerState.UNINITIALIZED
        self._coordinator: Any = None  # Lazy-loaded
        self._profiles: dict[Subsystem, SubsystemProfile] = dict(_DEFAULT_PROFILES)
        self._active_guards: int = 0
        self._telemetry = CoSchedulerTelemetry()
        # Per-subsystem semaphores for max_concurrent enforcement
        self._subsystem_semaphores: dict[Subsystem, asyncio.Semaphore] = {}
        for sub, profile in self._profiles.items():
            if profile.max_concurrent > 0:
                self._subsystem_semaphores[sub] = asyncio.Semaphore(profile.max_concurrent)

    def _get_lock(self) -> asyncio.Lock:
        """Thread-safe lazy init for asyncio.Lock."""
        lock = self._lock
        if lock is None:
            with self._lock_factory:
                lock = self._lock
                if lock is None:
                    lock = asyncio.Lock()
                    self._lock = lock
        return lock

    def _ensure_coordinator(self) -> Any:
        """Lazy-load the peak load coordinator."""
        if self._coordinator is None:
            from hledac.universal.core.peak_load_coordinator import (
                get_peak_coordinator,
                ResourceClass,
                TaskPriority,
            )
            self._coordinator = get_peak_coordinator()
            self._ResourceClass = ResourceClass
            self._TaskPriority = TaskPriority
        return self._coordinator

    @property
    def state(self) -> CoSchedulerState:
        return self._state

    # -- Lifecycle ------------------------------------------------------------

    async def start(
        self,
        sprint_deadline_s: float | None = None,
    ) -> None:
        """Initialize the co-scheduler for a sprint.

        Args:
            sprint_deadline_s: Sprint duration in seconds (for deadline boosting).
        """
        self._ensure_coordinator()

        # Set sprint deadline for priority boosting
        if sprint_deadline_s is not None:
            from hledac.universal.core.peak_load_coordinator import set_sprint_deadline
            set_sprint_deadline(sprint_deadline_s)

        self._state = CoSchedulerState.ACTIVE
        self._telemetry = CoSchedulerTelemetry(state=CoSchedulerState.ACTIVE)

        logger.info(
            f"[CoScheduler] Started (sprint_deadline={sprint_deadline_s}s, "
            f"subsystems={len(self._profiles)})"
        )

    async def shutdown(self) -> None:
        """Shutdown the co-scheduler.

        All pending admission waiters are released and
        the sprint deadline is cleared.
        """
        self._state = CoSchedulerState.SHUTDOWN

        # Clear sprint deadline
        from hledac.universal.core.peak_load_coordinator import set_sprint_deadline
        set_sprint_deadline(None)

        logger.info(
            f"[CoScheduler] Shutdown complete "
            f"(admissions={self._telemetry.total_admissions}, "
            f"rejections={self._telemetry.total_rejections})"
        )

    # -- Core admission protocol -----------------------------------------------

    @asynccontextmanager
    async def guard(
        self,
        subsystem: Subsystem,
        estimated_mb: float | None = None,
        *,
        priority: str | None = None,
        timeout_s: float | None = None,
        owner: str = "",
    ):
        """Acquire admission for a subsystem operation.

        This is the canonical entry point for ALL memory-intensive operations.
        Each subsystem that allocates > 100 MB MUST use this guard.

        Args:
            subsystem: The Subsystem enum value
            estimated_mb: Peak memory estimate (default: from profile)
            priority: "critical" | "high" | "normal" | "low" (default: from profile)
            timeout_s: Max wait time (default: from profile)
            owner: Human-readable identifier for debugging

        Yields:
            AdmissionContext with diagnostic info

        Raises:
            TimeoutError: if admission cannot be granted within timeout
            RuntimeError: if co-scheduler is shut down

        Usage:
            scheduler = get_co_scheduler()
            async with scheduler.guard(Subsystem.MLX_INFERENCE) as ctx:
                result = await engine.generate(prompt)
        """
        # Resolve profile defaults
        profile = self._profiles.get(subsystem)
        if profile is None:
            logger.warning(f"[CoScheduler] Unknown subsystem {subsystem}, using defaults")
            profile = SubsystemProfile(
                subsystem=subsystem,
                estimated_mb=estimated_mb or 500.0,
                default_priority=priority or "normal",
                timeout_s=timeout_s or 10.0,
            )

        est_mb = estimated_mb if estimated_mb is not None else profile.estimated_mb
        prio = priority if priority is not None else profile.default_priority
        timeout = timeout_s if timeout_s is not None else profile.timeout_s
        owner_str = owner or subsystem.value

        # Check state
        if self._state == CoSchedulerState.SHUTDOWN:
            raise RuntimeError(f"CoScheduler is shut down, rejecting {subsystem}")

        if self._state == CoSchedulerState.UNINITIALIZED:
            # Auto-initialize (fail-open for backward compat)
            await self.start()

        # Per-subsystem semaphore enforcement
        sem = self._subsystem_semaphores.get(subsystem)
        if sem is None:
            sem = contextlib.nullcontext()

        start_time = time.monotonic()
        self._active_guards += 1
        self._telemetry.active_guards = self._active_guards

        peak_guard = None
        try:
            # Layer 1: Per-subsystem concurrency limit
            async with sem:
                # Layer 2: Global peak-load admission
                coordinator = self._ensure_coordinator()

                from hledac.universal.core.peak_load_coordinator import (
                    ResourceClass,
                    TaskPriority,
                )
                resource_class_name = _SUBSYSTEM_TO_RESOURCE.get(subsystem, "network_fetch")
                resource_class = ResourceClass(resource_class_name)

                # Map priority string to TaskPriority
                priority_map = {
                    "critical": TaskPriority.CRITICAL,
                    "high": TaskPriority.HIGH,
                    "normal": TaskPriority.NORMAL,
                    "low": TaskPriority.LOW,
                }
                task_priority = priority_map.get(prio, TaskPriority.NORMAL)

                try:
                    peak_guard = await coordinator.acquire(
                        resource_class,
                        estimated_mb=est_mb,
                        priority=task_priority,
                        owner=f"{subsystem.value}:{owner_str}",
                        timeout_s=timeout,
                    )
                except TimeoutError:
                    self._telemetry.total_timeouts += 1
                    self._telemetry.total_rejections += 1
                    raise

                wait_time = time.monotonic() - start_time
                self._telemetry.total_admissions += 1

                # Track peak concurrent allocation
                snap = coordinator.snapshot()
                total_gib = snap.total_allocated_gib
                if total_gib > self._telemetry.peak_concurrent_mb:
                    self._telemetry.peak_concurrent_mb = total_gib

                # Yield admission context
                ctx = AdmissionContext(
                    subsystem=subsystem.value,
                    allocated_mb=est_mb,
                    wait_time_s=wait_time,
                    peak_utilization=coordinator.snapshot().utilization_fraction,
                    mutex_held=None,  # Filled by coordinator internally
                )

                yield ctx

        except asyncio.CancelledError:
            self._telemetry.total_preemptions += 1
            raise
        except TimeoutError:
            raise
        except Exception:
            logger.exception(f"[CoScheduler] Unexpected error for {subsystem}")
            # Fail-open: yield without admission context
            yield AdmissionContext(
                subsystem=subsystem.value,
                allocated_mb=0.0,
                wait_time_s=0.0,
                peak_utilization=0.0,
                mutex_held=None,
            )
        finally:
            if peak_guard is not None:
                try:
                    await peak_guard.__aexit__(None, None, None)
                except Exception:
                    pass
            self._active_guards -= 1
            self._telemetry.active_guards = self._active_guards
            self._telemetry.last_snapshot_time = time.monotonic()

    # -- Telemetry ------------------------------------------------------------

    def snapshot(self) -> CoSchedulerTelemetry:
        """Return current telemetry snapshot."""
        self._telemetry.state = self._state
        return self._telemetry

    def get_subsystem_profile(self, subsystem: Subsystem) -> SubsystemProfile | None:
        """Return the registered profile for a subsystem."""
        return self._profiles.get(subsystem)

    def register_subsystem(self, profile: SubsystemProfile) -> None:
        """Register a new subsystem profile (for extension).
        
        Must be called before start().
        """
        self._profiles[profile.subsystem] = profile
        if profile.max_concurrent > 0 and profile.subsystem not in self._subsystem_semaphores:
            self._subsystem_semaphores[profile.subsystem] = asyncio.Semaphore(profile.max_concurrent)

    # -- Integration with M1ResourceGovernor -----------------------------------

    async def on_pressure_change(self, uma_state: str) -> None:
        """Callback from M1ResourceGovernor when UMA pressure changes.

        UNIFIED-003: Propagates pressure state to the peak load coordinator
        for dynamic hard-limit adjustment.
        """
        # Only skip on SHUTDOWN — DEGRADED must allow recovery
        if self._state == CoSchedulerState.SHUTDOWN:
            return
        if self._state == CoSchedulerState.UNINITIALIZED:
            return  # Not started yet, nothing to do

        coordinator = self._ensure_coordinator()
        try:
            if uma_state in ("critical", "emergency"):
                # Trigger preemption + degrade
                await coordinator.preempt_low_priority(target_mb=500.0)
                self._state = CoSchedulerState.DEGRADED
            elif self._state == CoSchedulerState.DEGRADED:
                # Recover from degraded state when pressure drops
                self._state = CoSchedulerState.ACTIVE
        except Exception:
            pass  # Fail-soft


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_co_scheduler: GlobalPeakCoScheduler | None = None
_co_scheduler_lock = threading.Lock()


def get_co_scheduler() -> GlobalPeakCoScheduler:
    """Get or create the singleton GlobalPeakCoScheduler.

    This is the canonical entry point for all subsystems. Every operation
    allocating > 100 MB MUST call: scheduler = get_co_scheduler()

    Thread-safe: uses double-checked locking.
    """
    global _co_scheduler
    if _co_scheduler is None:
        with _co_scheduler_lock:
            if _co_scheduler is None:
                _co_scheduler = GlobalPeakCoScheduler()
    return _co_scheduler


async def start_co_scheduler(sprint_deadline_s: float | None = None) -> GlobalPeakCoScheduler:
    """Initialize and start the co-scheduler.

    Call this at sprint startup (from SprintScheduler.run_prelude()).

    Args:
        sprint_deadline_s: Sprint duration in seconds for deadline-aware boosting.
    """
    scheduler = get_co_scheduler()
    await scheduler.start(sprint_deadline_s=sprint_deadline_s)
    return scheduler


async def shutdown_co_scheduler() -> None:
    """Shutdown the co-scheduler. Call at sprint teardown (from winddown)."""
    scheduler = get_co_scheduler()
    await scheduler.shutdown()


def reset_co_scheduler() -> None:
    """Reset the singleton. For testing only."""
    global _co_scheduler
    with _co_scheduler_lock:
        _co_scheduler = None
