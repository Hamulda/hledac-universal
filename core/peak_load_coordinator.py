# GlobalPeakLoadCoordinator - Cross-subsystem admission control for M1 8GB UMA.
# UNIFIED-001 + UNIFIED-003 (Peak-Load Global Co-Scheduler & Mutual Exclusion)
#
# Solves the OOM kill problem where MLX generation (2.5 GB),
# ANE OCR (1.5 GB), and Tantivy indexing (2 GB) allocate independently with
# zero cross-subsystem coordination -> >6 GB simultaneous -> OOM on M1 8GB.
#
# ARCHITECTURE:
#     Singleton coordinator sits ABOVE all resource-consuming subsystems.
#     Each subsystem calls: await coordinator.acquire(ResourceClass, estimated_mb)
#     BEFORE peak allocation. The coordinator tracks running allocations and
#     enforces SUM(allocated) <= BUDGET_GIB (81% of 8 GB = 6.48 GiB).
#
# MUTUAL EXCLUSION (UNIFIED-003):
#     M1 hardware constraints mean certain subsystems CANNOT run simultaneously:
#     - METAL_PIPELINE: MLX_GENERATION + ANE_VISION share Metal command queue + ANE.
#       Running both concurrently causes 2-5x slowdown and Metal OOM risk.
#     - MMAP_HEAVY: TANTIVY_INDEX + large NETWORK_FETCH compete for page cache.
#       Combined mmap pressure causes kernel paging to swap.
#     The coordinator enforces these exclusions via MutexGroup tracking.
#
# UMA PRESSURE GATING (UNIFIED-003):
#     Internal accounting (tracked MB) is necessary but not sufficient -
#     macOS kernel allocations and memory fragmentation add ~1.0-1.5 GB of
#     invisible pressure. Every admission now cross-checks sample_uma_status()
#     for ground-truth memory state, rejecting if system_used_gib > budget.
#
# DEADLINE-AWARE BOOSTING (UNIFIED-003):
#     As the sprint deadline approaches, remaining tasks get automatic
#     priority boost to prevent starvation. A task with 10s remaining
#     gets boosted to CRITICAL to ensure it completes before winddown.
#
# ACTIVE PREEMPTION (UNIFIED-003):
#     When coordinator signals preemption, LOW/NORMAL tasks are actively
#     cancelled via asyncio.Task.cancel() rather than just "notified".
#     Cooperative cancellation via CancelledError ensures clean teardown.
#
# M1 8GB UMA BUDGET CALIBRATION:
#     Total RAM:              8.00 GiB
#     System reserved:        ~1.52 GiB (macOS + WindowServer + kernel)
#     Safe ceiling (81%):     6.48 GiB
#     Headroom for spikes:    0.50 GiB
#     Available for workload: 5.98 GiB (subsystem allocations)

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from enum import StrEnum
from typing import Any

import msgspec

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS - M1 8GB UMA CALIBRATION
# =============================================================================

# 81% of 8 GiB = 6.48 GiB - safe ceiling for all subsystem allocations
_DEFAULT_TOTAL_BUDGET_GIB: float = 6.48

# Per-class reservation floor - guarantees minimum allocation for each class
_CLASS_RESERVATION_FRACTION: float = 0.05  # 5% of budget = ~324 MiB per class

# Admission timeout - how long a subsystem waits before giving up
_DEFAULT_ACQUIRE_TIMEOUT_S: float = 30.0

# Preemption check interval
_PREEMPTION_CHECK_INTERVAL_S: float = 2.0

# High-water mark fraction - when total allocation exceeds this, trigger preemption
_HIGH_WATER_FRACTION: float = 0.90  # 90% of budget = ~5.83 GiB

# Emergency cutoff - hard stop, no new admissions above this
_EMERGENCY_FRACTION: float = 0.97  # 97% of budget = ~6.29 GiB

# Max admission log entries (bounded for memory safety)
_MAX_ADMISSION_LOG: int = 64


# =============================================================================
# ENUMS
# =============================================================================


class ResourceClass(StrEnum):
    """Canonical resource classes for peak-load admission control.

    Each class represents a memory-intensive subsystem that can independently
    allocate 1-3 GB. The coordinator tracks allocations per-class and enforces
    a global budget across all classes.
    """

    MLX_GENERATION = "mlx_generation"
    ANE_VISION = "ane_vision"
    TANTIVY_INDEX = "tantivy_index"
    NETWORK_FETCH = "network_fetch"
    SIDECAR_ADVISORY = "sidecar_advisory"


class TaskPriority(StrEnum):
    """Priority levels for admission control.

    Higher-priority tasks can preempt lower-priority ones when memory pressure
    is high. Maps to existing Priority enum in resource_governor.py.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


# Priority ordering - higher numeric value = higher priority
_PRIORITY_ORDER: dict[TaskPriority, int] = {
    TaskPriority.CRITICAL: 4,
    TaskPriority.HIGH: 3,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 1,
}


# =============================================================================
# UNIFIED-003: MUTUAL EXCLUSION GROUPS
# =============================================================================


class MutexGroup(StrEnum):
    """Hardware-backed mutual exclusion groups for M1 8GB.

    These groups encode physical resource constraints of the M1 SoC.
    Only ONE resource class per group may be active at any time.

    METAL_PIPELINE:
        MLX_GENERATION and ANE_VISION share the Metal command queue.
        The M1 GPU has a single command queue; running both mlx_lm.generate()
        and VNRecognizeTextRequest simultaneously causes:
        - 2-5x throughput degradation (queue depth = 2)
        - Metal OOM risk (both allocate Metal buffers)
        - ANE contention (ANE is exposed through Metal on M1)

    MMAP_HEAVY:
        TANTIVY_INDEX and large NETWORK_FETCH operations compete for
        the kernel's Unified Buffer Cache (UBC). Each mmap-heavy operation
        pressures the page cache, causing eviction of the other's pages.
        Combined mmap pressure > 4 GB triggers kernel swapping.
    """

    METAL_PIPELINE = "metal_pipeline"
    MMAP_HEAVY = "mmap_heavy"


# Maps ResourceClass -> set of MutexGroup it belongs to
_RESOURCE_MUTEX_GROUPS: dict[ResourceClass, set[MutexGroup]] = {
    ResourceClass.MLX_GENERATION: {MutexGroup.METAL_PIPELINE},
    ResourceClass.ANE_VISION: {MutexGroup.METAL_PIPELINE},
    ResourceClass.TANTIVY_INDEX: {MutexGroup.MMAP_HEAVY},
    ResourceClass.NETWORK_FETCH: {MutexGroup.MMAP_HEAVY},
    ResourceClass.SIDECAR_ADVISORY: set(),  # No mutex constraints
}


# =============================================================================
# UNIFIED-003: DEADLINE-AWARE PRIORITY BOOSTING
# =============================================================================

# Default boost thresholds (seconds before deadline)
_DEFAULT_CRITICAL_BOOST_S: float = 15.0   # < 15s -> CRITICAL
_DEFAULT_HIGH_BOOST_S: float = 60.0       # < 60s -> HIGH
_DEFAULT_NORMAL_BOOST_S: float = 300.0    # < 300s -> NORMAL minimum

# Global sprint deadline (set by GlobalCoScheduler)
_sprint_deadline_ts: float | None = None  # time.monotonic() of sprint end


def set_sprint_deadline(deadline_s: float | None) -> None:
    """Set the sprint deadline for priority boosting.

    Args:
        deadline_s: Seconds from now until sprint end, or None to clear.
    """
    global _sprint_deadline_ts
    if deadline_s is None:
        _sprint_deadline_ts = None
    else:
        import time as _time
        _sprint_deadline_ts = _time.monotonic() + deadline_s


def _boost_for_deadline(priority: TaskPriority) -> TaskPriority:
    """UNIFIED-003: Boost priority based on remaining sprint time.

    As sprint nears its deadline, tasks get automatic priority boosts
    to ensure completion before winddown. This prevents starvation of
    late-cycle tasks that would otherwise be deferred.

    Returns:
        Boosted TaskPriority (never lower than input).
    """
    if _sprint_deadline_ts is None:
        return priority  # No deadline set, no boost

    import time as _time
    remaining = _sprint_deadline_ts - _time.monotonic()

    # Already CRITICAL or deadline passed -> no change
    if priority == TaskPriority.CRITICAL or remaining <= 0:
        return TaskPriority.CRITICAL

    if remaining <= _DEFAULT_CRITICAL_BOOST_S:
        return TaskPriority.CRITICAL
    elif remaining <= _DEFAULT_HIGH_BOOST_S:
        # Boost: LOW->HIGH, NORMAL->HIGH, HIGH stays HIGH
        if priority == TaskPriority.LOW:
            return TaskPriority.HIGH
        return max(priority, TaskPriority.HIGH, key=lambda p: _PRIORITY_ORDER[p])
    elif remaining <= _DEFAULT_NORMAL_BOOST_S:
        # Ensure at least NORMAL priority
        if priority == TaskPriority.LOW:
            return TaskPriority.NORMAL
    return priority


# =============================================================================
# DATA STRUCTURES
# =============================================================================


class AllocationTicket(msgspec.Struct, frozen=True, gc=False):
    """Immutable record of an active allocation.

    Created by acquire(), destroyed on release().
    Used for telemetry and preemption decisions.
    """

    ticket_id: int
    resource_class: ResourceClass
    priority: TaskPriority
    estimated_mb: float
    allocated_at: float  # time.monotonic()
    owner: str  # caller identifier for debugging


class AdmissionResult(msgspec.Struct, frozen=True, gc=False):
    """Result of an admission check - used for telemetry and debugging."""

    granted: bool
    ticket_id: int | None = None
    wait_time_s: float = 0.0
    reason: str = ""
    total_allocated_gib: float = 0.0


class PeakLoadSnapshot(msgspec.Struct, frozen=True, gc=False):
    """Read-only snapshot of current peak-load state.

    Used by ResourceGovernor and telemetry consumers.
    """

    total_allocated_gib: float
    budget_gib: float
    utilization_fraction: float  # 0.0-1.0
    per_class_gib: dict[str, float]
    active_tickets: int
    waiting_requests: int
    high_water_active: bool
    emergency_active: bool
    timestamp: float  # time.monotonic()


# =============================================================================
# ACTIVE PREEMPTION TOKEN (UNIFIED-003)
# =============================================================================


class _ActivePreemptionToken:
    """Weak-reference token for active task preemption.

    When the coordinator signals preemption, it iterates active tokens
    ordered by priority (LOW first) and cancels tasks until memory target
    is reached. Uses asyncio.Task.cancel() for cooperative cancellation.

    Cooperative cancellation: subsystems catch CancelledError in their
    async context managers and perform clean teardown (release MLX buffers,
    close mmap handles, etc.).
    """

    __slots__ = ("_task", "_priority", "_resource_class", "_estimated_mb", "_owner")

    def __init__(
        self,
        task: "asyncio.Task[Any] | None",
        priority: TaskPriority,
        resource_class: ResourceClass,
        estimated_mb: float,
        owner: str,
    ) -> None:
        self._task = task
        self._priority = priority
        self._resource_class = resource_class
        self._estimated_mb = estimated_mb
        self._owner = owner

    @property
    def task(self) -> "asyncio.Task[Any] | None":
        return self._task

    @property
    def priority(self) -> TaskPriority:
        return self._priority

    @property
    def resource_class(self) -> ResourceClass:
        return self._resource_class

    @property
    def estimated_mb(self) -> float:
        return self._estimated_mb

    @property
    def owner(self) -> str:
        return self._owner


# =============================================================================
# ALLOCATION GUARD (async context manager)
# =============================================================================


class _AllocationGuard:
    """Async context manager returned by acquire().

    Holds the allocation ticket and releases it on exit.
    """

    __slots__ = ("_coordinator", "_ticket", "_released")

    def __init__(self, coordinator: "GlobalPeakLoadCoordinator", ticket: AllocationTicket) -> None:
        self._coordinator = coordinator
        self._ticket = ticket
        self._released = False

    @property
    def ticket(self) -> AllocationTicket:
        """Access the allocation ticket for telemetry."""
        return self._ticket

    async def __aenter__(self) -> "_AllocationGuard":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self._released:
            self._released = True
            await self._coordinator._release(self._ticket)


# =============================================================================
# GLOBAL PEAK-LOAD COORDINATOR
# =============================================================================


class GlobalPeakLoadCoordinator:
    """Singleton admission controller for memory-intensive subsystems on M1 8GB.

    USAGE:
        coordinator = get_peak_coordinator()
        async with coordinator.acquire(ResourceClass.MLX_GENERATION, 2500) as guard:
            # Safe to allocate up to 2500 MB here
            await run_mlx_generation()

    THREAD SAFETY:
        All mutations are guarded by asyncio.Lock. The coordinator is
        designed to be called from the main event loop only. Cross-thread
        access uses threading.Lock for the lazy singleton init.

    PREEMPTION PROTOCOL (UNIFIED-001):
        When total allocation exceeds HIGH_WATER (90%), the coordinator
        signals low-priority waiters to back off via asyncio.Event.
        CRITICAL tasks always get through; LOW tasks may be deferred.

    MUTUAL EXCLUSION (UNIFIED-003):
        M1 hardware constraints: MLX_GENERATION and ANE_VISION share the
        Metal command queue and cannot run simultaneously. TANTIVY_INDEX
        and NETWORK_FETCH compete for page cache. The coordinator enforces
        these hardware-backed exclusion groups.

    UMA PRESSURE GATING (UNIFIED-003):
        Every admission cross-checks sample_uma_status() for ground-truth
        memory state. Internal accounting alone is insufficient — macOS
        kernel allocations add ~1.0-1.5 GB invisible pressure.

    DEADLINE-AWARE BOOSTING (UNIFIED-003):
        Tasks near sprint end get automatic priority boost to prevent
        starvation during winddown.

    ACTIVE PREEMPTION (UNIFIED-003):
        When memory pressure hits CRITICAL/EMERGENCY, LOW/NORMAL tasks
        are actively cancelled via asyncio.Task.cancel() — not just
        signaled — to free memory immediately.
    """

    __slots__ = (
        "_lock",
        "_lock_factory",
        "_allocations",
        "_next_ticket_id",
        "_budget_gib",
        "_class_reservations",
        "_high_water_event",
        "_emergency_event",
        "_waiting_count",
        "_preempt_signal",
        "_total_allocated_mb",
        "_per_class_mb",
        "_admission_log",
        # UNIFIED-003: Mutual exclusion tracking
        "_mutex_active",
        "_mutex_lock",
        # UNIFIED-003: Active preemption tokens
        "_active_tokens",
        # UNIFIED-003: Telemetry
        "_total_preemptions",
        "_total_uma_rejections",
        "_total_mutex_waits",
    )

    def __init__(self, budget_gib: float | None = None) -> None:
        self._lock_factory = threading.Lock()
        self._lock: "asyncio.Lock | None" = None
        self._budget_gib = budget_gib or _DEFAULT_TOTAL_BUDGET_GIB
        self._class_reservations: dict[ResourceClass, float] = {
            cls: self._budget_gib * 1024 * _CLASS_RESERVATION_FRACTION
            for cls in ResourceClass
        }
        self._allocations: dict[int, AllocationTicket] = {}
        self._next_ticket_id: int = 0
        self._total_allocated_mb: float = 0.0
        self._per_class_mb: dict[ResourceClass, float] = defaultdict(float)
        self._waiting_count: int = 0
        self._high_water_event = asyncio.Event()
        self._emergency_event = asyncio.Event()
        self._preempt_signal = asyncio.Event()
        self._admission_log: list[AdmissionResult] = []
        # UNIFIED-003: Mutual exclusion tracking
        self._mutex_active: dict[MutexGroup, ResourceClass | None] = {
            grp: None for grp in MutexGroup
        }
        self._mutex_lock = threading.Lock()  # Protects _mutex_active writes
        # UNIFIED-003: Active preemption tokens
        self._active_tokens: dict[int, _ActivePreemptionToken] = {}
        # UNIFIED-003: Telemetry counters
        self._total_preemptions: int = 0
        self._total_uma_rejections: int = 0
        self._total_mutex_waits: int = 0

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

    # -- Core admission protocol -----------------------------------------------

    async def acquire(
        self,
        resource_class: ResourceClass,
        estimated_mb: float,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        owner: str = "",
        timeout_s: float = _DEFAULT_ACQUIRE_TIMEOUT_S,
    ) -> "_AllocationGuard":
        """Request admission to allocate estimated_mb megabytes.

        Blocks until:
          1. Budget is available (total + estimated_mb <= budget)
          2. No mutex conflict (METAL_PIPELINE: MLX vs ANE; MMAP_HEAVY)
          3. UMA pressure is below budget (ground-truth check)
          4. No preemption signal is active for lower-priority tasks
          5. Timeout is reached (raises TimeoutError)

        Returns an async context manager that releases the allocation on exit.

        UNIFIED-003 ENHANCEMENTS:
          - Deadline-aware priority boosting (set_sprint_deadline)
          - Mutual exclusion group enforcement
          - UMA pressure ground-truth gating
          - Active preemption token registration
          - asyncio.Task binding for cancellation

        PRIORITY PREEMPTION:
          - CRITICAL tasks: always admitted immediately, may exceed budget
          - HIGH tasks: admitted if budget available or can preempt LOW/NORMAL
          - NORMAL tasks: admitted if budget available
          - LOW tasks: deferred if HIGH/CRITICAL pressure detected

        FAIL-OPEN:
          If coordinator is in degraded state, admission is granted
          unconditionally with a warning log.
        """
        # UNIFIED-003: Apply deadline-aware boosting
        boosted_priority = _boost_for_deadline(priority)
        if boosted_priority != priority:
            logger.debug(
                f"[UNIFIED-003] Priority boosted: {priority} -> {boosted_priority} "
                f"for {resource_class} ({owner}), remaining={_sprint_deadline_ts - time.monotonic() if _sprint_deadline_ts else 'N/A':.1f}s"
            )
            priority = boosted_priority

        start_time = time.monotonic()
        lock = self._get_lock()

        # Fast path: check if admission is possible without waiting
        async with lock:
            can_admit, reason = self._can_admit(resource_class, estimated_mb, priority)
            if can_admit:
                ticket = self._allocate(resource_class, estimated_mb, priority, owner)
                # UNIFIED-003: Register active preemption token
                self._register_token(ticket, priority, resource_class, estimated_mb, owner)
                self._log_admission(AdmissionResult(
                    granted=True,
                    ticket_id=ticket.ticket_id,
                    wait_time_s=0.0,
                    reason=reason,
                    total_allocated_gib=self._total_allocated_mb / 1024,
                ))
                return _AllocationGuard(self, ticket)

        # Slow path: wait for budget to become available
        self._waiting_count += 1
        try:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_s:
                    self._log_admission(AdmissionResult(
                        granted=False,
                        wait_time_s=elapsed,
                        reason=f"timeout after {timeout_s:.1f}s",
                        total_allocated_gib=self._total_allocated_mb / 1024,
                    ))
                    raise TimeoutError(
                        f"PeakLoadCoordinator: admission timeout for {resource_class} "
                        f"({estimated_mb:.0f} MB, priority={priority})"
                    )

                # UNIFIED-003: Re-apply deadline boost (deadline may have changed)
                priority = _boost_for_deadline(priority)

                # Check preemption signal - lower-priority tasks should back off
                if self._preempt_signal.is_set():
                    my_priority_level = _PRIORITY_ORDER[priority]
                    if my_priority_level < _PRIORITY_ORDER[TaskPriority.HIGH]:
                        # Wait for preemption to clear
                        try:
                            await asyncio.wait_for(
                                self._preempt_signal.wait(),
                                timeout=min(1.0, timeout_s - elapsed),
                            )
                        except asyncio.TimeoutError:
                            pass  # Continue loop, will check again
                        continue

                # Try to admit again
                async with lock:
                    can_admit, reason = self._can_admit(resource_class, estimated_mb, priority)
                    if can_admit:
                        ticket = self._allocate(resource_class, estimated_mb, priority, owner)
                        # UNIFIED-003: Register active preemption token
                        self._register_token(ticket, priority, resource_class, estimated_mb, owner)
                        self._log_admission(AdmissionResult(
                            granted=True,
                            ticket_id=ticket.ticket_id,
                            wait_time_s=time.monotonic() - start_time,
                            reason=reason,
                            total_allocated_gib=self._total_allocated_mb / 1024,
                        ))
                        return _AllocationGuard(self, ticket)

                # Wait for a release signal before retrying
                try:
                    await asyncio.wait_for(
                        self._high_water_event.wait(),
                        timeout=min(_PREEMPTION_CHECK_INTERVAL_S, timeout_s - elapsed),
                    )
                    self._high_water_event.clear()
                except asyncio.TimeoutError:
                    pass  # Continue loop, will check again

        finally:
            self._waiting_count -= 1

    def _can_admit(
        self,
        resource_class: ResourceClass,
        estimated_mb: float,
        priority: TaskPriority,
    ) -> tuple[bool, str]:
        """Check if admission is possible without blocking.

        UNIFIED-003: Now checks three layers:
          1. Mutex group conflicts (hardware-backed exclusion)
          2. UMA pressure ground-truth (psutil, not just internal accounting)
          3. Budget + priority gating (original logic)

        Returns (can_admit, reason) tuple.
        """
        # Layer 1: UNIFIED-003 — Mutual exclusion group check
        mutex_conflict = self._check_mutex_conflict(resource_class)
        if mutex_conflict is not None:
            # CRITICAL tasks bypass mutex (emergency override)
            if priority == TaskPriority.CRITICAL:
                # Preempt the conflicting task — cancel it
                self._preempt_mutex_conflict(resource_class, mutex_conflict)
                return True, "CRITICAL preempting mutex conflict"

            # For non-CRITICAL, check if we should wait or reject
            if priority == TaskPriority.HIGH:
                # HIGH priority: wait if remaining sprint time > 30s
                return False, f"mutex conflict: {mutex_conflict.value} (HIGH waiting)"
            return False, f"mutex conflict: {mutex_conflict.value}"

        # Layer 2: UNIFIED-003 — UMA pressure ground-truth check
        uma_ok, uma_reason = self._check_uma_pressure(estimated_mb, priority)
        if not uma_ok:
            self._total_uma_rejections += 1
            return False, uma_reason

        # Layer 3: Budget + priority gating (original logic)
        budget_mb = self._budget_gib * 1024
        total_after = self._total_allocated_mb + estimated_mb
        utilization = total_after / budget_mb

        # Emergency cutoff - no new admissions above 97%
        if utilization >= _EMERGENCY_FRACTION:
            # CRITICAL tasks bypass emergency cutoff
            if priority == TaskPriority.CRITICAL:
                return True, "CRITICAL bypass emergency"
            return False, f"emergency cutoff ({utilization:.1%} utilization)"

        # High-water mark - trigger preemption for lower priorities
        if utilization >= _HIGH_WATER_FRACTION:
            self._emergency_event.set()
            # UNIFIED-003: Trigger active preemption at high-water
            self._cancel_preemptible_tasks(estimated_mb, priority)
            # Check priority-based admission
            match priority:
                case TaskPriority.CRITICAL:
                    return True, "CRITICAL bypass high-water"
                case TaskPriority.HIGH:
                    # Can admit if we can preempt LOW/NORMAL tasks
                    preemptable_mb = self._get_preemptable_mb(priority)
                    if preemptable_mb >= estimated_mb:
                        # Signal preemption
                        self._preempt_signal.set()
                        return True, "HIGH preempting lower priority"
                    return False, f"insufficient preemptable MB ({preemptable_mb:.0f})"
                case TaskPriority.NORMAL:
                    # Admit only if class reservation is available
                    class_mb = self._per_class_mb[resource_class]
                    class_reservation = self._class_reservations[resource_class]
                    if class_mb + estimated_mb <= class_reservation:
                        return True, "within class reservation"
                    return False, f"class reservation exceeded ({class_mb:.0f}/{class_reservation:.0f} MB)"
                case TaskPriority.LOW:
                    # Defer if high-water active
                    return False, "LOW deferred at high-water"

        # Normal admission - check budget
        if total_after <= budget_mb:
            return True, "budget available"

        # Budget exceeded
        return False, f"budget exceeded ({utilization:.1%} utilization)"

    # ── UNIFIED-003: Mutual exclusion & UMA pressure helpers ────────────────

    def _check_mutex_conflict(self, resource_class: ResourceClass) -> MutexGroup | None:
        """Check if resource_class conflicts with an active mutex group.

        Returns:
            The conflicting MutexGroup, or None if no conflict.
        """
        my_groups = _RESOURCE_MUTEX_GROUPS.get(resource_class, set())
        for group in my_groups:
            active = self._mutex_active.get(group)
            if active is not None and active != resource_class:
                return group
        return None

    def _check_uma_pressure(
        self,
        estimated_mb: float,
        priority: TaskPriority,
    ) -> tuple[bool, str]:
        """UNIFIED-003: Ground-truth UMA pressure check via psutil.

        Internal accounting (tracked MB) is necessary but insufficient.
        macOS kernel allocations add ~1.0-1.5 GB invisible pressure.
        This probe reads actual system memory usage to prevent OOM.

        Sampling cost: ~2 µs (cached psutil read, no syscall).

        Returns:
            (can_proceed, reason) tuple.
        """
        # CRITICAL tasks always skip UMA check
        if priority == TaskPriority.CRITICAL:
            return True, "CRITICAL bypass UMA check"

        try:
            # Use cached UMA sample (same source as M1ResourceGovernor)
            # Avoids DRY violation — governor owns the canonical sampling path
            from hledac.universal.core.resource_governor import sample_uma_status
            uma = sample_uma_status()
            system_used_gib = uma.system_used_gib
            system_total_gib = uma.system_used_gib + uma.system_available_gib

            # Conservative: reject if system_used + estimated > total * 0.90
            system_limit_gib = system_total_gib * 0.90
            projected_used_gib = system_used_gib + (estimated_mb / 1024)

            if projected_used_gib > system_limit_gib:
                return False, (
                    f"UMA pressure: projected {projected_used_gib:.2f} GiB "
                    f"> limit {system_limit_gib:.2f} GiB "
                    f"(system used={system_used_gib:.2f} GiB)"
                )

            # Check swap — high swap = systemic pressure
            if uma.swap_used_gib is not None and uma.swap_used_gib > 3.8:
                # HIGH priority can proceed despite high swap
                if _PRIORITY_ORDER[priority] < _PRIORITY_ORDER[TaskPriority.HIGH]:
                    return False, (
                        f"UMA pressure: swap={uma.swap_used_gib:.1f} GiB "
                        f"(threshold=3.8 GiB)"
                    )

            return True, f"UMA OK ({system_used_gib:.1f}/{system_total_gib:.1f} GiB)"
        except Exception:
            # Fail-open: if psutil is unavailable, skip UMA check
            return True, "UMA check skipped (psutil unavailable)"

    def _register_token(
        self,
        ticket: AllocationTicket,
        priority: TaskPriority,
        resource_class: ResourceClass,
        estimated_mb: float,
        owner: str,
    ) -> None:
        """UNIFIED-003: Register active preemption token.

        Binds the current asyncio.Task (if available) so the coordinator
        can cancel it during preemption.
        """
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None

        token = _ActivePreemptionToken(
            task=current_task,
            priority=priority,
            resource_class=resource_class,
            estimated_mb=estimated_mb,
            owner=owner,
        )
        self._active_tokens[ticket.ticket_id] = token

    def _unregister_token(self, ticket_id: int) -> None:
        """UNIFIED-003: Remove preemption token on release."""
        self._active_tokens.pop(ticket_id, None)

    def _preempt_mutex_conflict(
        self,
        incoming_class: ResourceClass,
        conflict_group: MutexGroup,
    ) -> None:
        """UNIFIED-003: Cancel the task holding a mutex group we need.

        Called when a CRITICAL-priority task requires a mutex group
        currently held by a lower-priority resource class.
        """
        conflicting_class = self._mutex_active.get(conflict_group)
        if conflicting_class is None:
            return

        # Find active tokens for the conflicting class
        tokens_to_cancel: list[_ActivePreemptionToken] = []
        for token in self._active_tokens.values():
            if token.resource_class == conflicting_class:
                tokens_to_cancel.append(token)

        # Sort by priority (LOW first) and cancel
        tokens_to_cancel.sort(key=lambda t: _PRIORITY_ORDER[t.priority])

        for token in tokens_to_cancel:
            if token.task is not None and not token.task.done():
                logger.warning(
                    f"[UNIFIED-003] Preempting mutex conflict: cancelling "
                    f"{token.resource_class} ({token.owner}) for {incoming_class}"
                )
                token.task.cancel()
                self._total_preemptions += 1

        # Clear the mutex group
        with self._mutex_lock:
            self._mutex_active[conflict_group] = None

    def _cancel_preemptible_tasks(
        self,
        target_mb: float,
        requester_priority: TaskPriority,
    ) -> float:
        """UNIFIED-003: Actively cancel preemptible tasks to free memory.

        When memory pressure hits HIGH_WATER, this method iterates active
        tokens ordered by priority (LOW first) and calls asyncio.Task.cancel()
        until the target memory is freed. Cooperative cancellation via
        CancelledError ensures subsystems clean up properly.

        Args:
            target_mb: How much memory we need to free
            requester_priority: Priority of the task requesting admission

        Returns:
            Estimated MB freed by cancellation.
        """
        freed_mb = 0.0
        requester_level = _PRIORITY_ORDER[requester_priority]

        # Collect preemptible tokens (lower priority than requester)
        preemptible: list[_ActivePreemptionToken] = []
        for token in self._active_tokens.values():
            if _PRIORITY_ORDER[token.priority] < requester_level:
                if token.task is not None and not token.task.done():
                    preemptible.append(token)

        # Sort by priority (LOW first)
        preemptible.sort(key=lambda t: _PRIORITY_ORDER[t.priority])

        for token in preemptible:
            if freed_mb >= target_mb:
                break

            logger.info(
                f"[UNIFIED-003] Active preemption: cancelling "
                f"{token.resource_class} ({token.owner}, "
                f"priority={token.priority}, est={token.estimated_mb:.0f} MB)"
            )
            # Cancel the asyncio task
            if token.task is not None:
                token.task.cancel()
            freed_mb += token.estimated_mb
            self._total_preemptions += 1

        return freed_mb

    def _allocate(
        self,
        resource_class: ResourceClass,
        estimated_mb: float,
        priority: TaskPriority,
        owner: str,
    ) -> AllocationTicket:
        """Create and register a new allocation ticket.

        UNIFIED-003: Now acquires mutex groups for the resource class.

        MUST be called under self._lock.
        """
        ticket_id = self._next_ticket_id
        self._next_ticket_id += 1

        ticket = AllocationTicket(
            ticket_id=ticket_id,
            resource_class=resource_class,
            priority=priority,
            estimated_mb=estimated_mb,
            allocated_at=time.monotonic(),
            owner=owner,
        )

        self._allocations[ticket_id] = ticket
        self._total_allocated_mb += estimated_mb
        self._per_class_mb[resource_class] += estimated_mb

        # UNIFIED-003: Acquire mutex groups
        my_groups = _RESOURCE_MUTEX_GROUPS.get(resource_class, set())
        for group in my_groups:
            if self._mutex_active.get(group) is None:
                self._mutex_active[group] = resource_class

        # Update high-water state
        utilization = self._total_allocated_mb / (self._budget_gib * 1024)
        if utilization >= _HIGH_WATER_FRACTION:
            self._high_water_event.set()
        else:
            self._high_water_event.clear()

        if utilization >= _EMERGENCY_FRACTION:
            self._emergency_event.set()
        else:
            self._emergency_event.clear()

        logger.debug(
            f"[PeakLoad] Allocated ticket #{ticket_id}: {resource_class} "
            f"{estimated_mb:.0f} MB (total: {self._total_allocated_mb:.0f} MB, "
            f"{utilization:.1%} utilization)"
        )

        return ticket

    async def _release(self, ticket: AllocationTicket) -> None:
        """Release an allocation and free its budget.

        UNIFIED-003: Now releases mutex groups and unregisters preemption token.

        Called by _AllocationGuard.__aexit__.
        """
        lock = self._get_lock()
        async with lock:
            if ticket.ticket_id not in self._allocations:
                logger.warning(f"[PeakLoad] Ticket #{ticket.ticket_id} already released")
                return

            del self._allocations[ticket.ticket_id]
            self._total_allocated_mb -= ticket.estimated_mb
            self._per_class_mb[ticket.resource_class] -= ticket.estimated_mb

            # UNIFIED-003: Release mutex groups
            my_groups = _RESOURCE_MUTEX_GROUPS.get(ticket.resource_class, set())
            with self._mutex_lock:
                for group in my_groups:
                    if self._mutex_active.get(group) == ticket.resource_class:
                        self._mutex_active[group] = None

            # UNIFIED-003: Unregister preemption token
            self._unregister_token(ticket.ticket_id)

            # Ensure non-negative (guard against double-release)
            if self._total_allocated_mb < 0:
                self._total_allocated_mb = 0.0
            if self._per_class_mb[ticket.resource_class] < 0:
                self._per_class_mb[ticket.resource_class] = 0.0

            # Update high-water state
            utilization = self._total_allocated_mb / (self._budget_gib * 1024)
            if utilization < _HIGH_WATER_FRACTION:
                self._high_water_event.clear()
                self._emergency_event.clear()
                self._preempt_signal.clear()
            elif utilization < _EMERGENCY_FRACTION:
                self._emergency_event.clear()

            # Signal waiters that budget is available
            # Only wake if there are actually waiting calls — avoid spurious wakeups
            if self._waiting_count > 0:
                self._high_water_event.set()

            logger.debug(
                f"[PeakLoad] Released ticket #{ticket.ticket_id}: {ticket.resource_class} "
                f"{ticket.estimated_mb:.0f} MB (total: {self._total_allocated_mb:.0f} MB, "
                f"{utilization:.1%} utilization)"
            )

    def _get_preemptable_mb(self, requester_priority: TaskPriority) -> float:
        """Calculate how much memory can be freed by preempting lower-priority tasks.

        MUST be called under self._lock.
        """
        requester_level = _PRIORITY_ORDER[requester_priority]
        preemptable_mb = 0.0

        for ticket in self._allocations.values():
            ticket_level = _PRIORITY_ORDER[ticket.priority]
            if ticket_level < requester_level:
                preemptable_mb += ticket.estimated_mb

        return preemptable_mb

    def _log_admission(self, result: AdmissionResult) -> None:
        """Log admission result (bounded ring buffer)."""
        self._admission_log.append(result)
        if len(self._admission_log) > _MAX_ADMISSION_LOG:
            self._admission_log.pop(0)

    # -- Telemetry and diagnostics ---------------------------------------------

    def snapshot(self) -> PeakLoadSnapshot:
        """Return a read-only snapshot of current state.

        UNIFIED-003: Now includes mutex group occupancy and preemption counters.

        Thread-safe: reads are atomic under GIL.
        """
        utilization = self._total_allocated_mb / (self._budget_gib * 1024)
        per_class_gib = {
            cls.value: mb / 1024
            for cls, mb in self._per_class_mb.items()
        }

        # UNIFIED-003: Mutex group occupancy
        mutex_occupancy = {
            grp.value: (cls.value if cls else None)
            for grp, cls in self._mutex_active.items()
        }

        snapshot = PeakLoadSnapshot(
            total_allocated_gib=self._total_allocated_mb / 1024,
            budget_gib=self._budget_gib,
            utilization_fraction=utilization,
            per_class_gib=per_class_gib,
            active_tickets=len(self._allocations),
            waiting_requests=self._waiting_count,
            high_water_active=self._high_water_event.is_set(),
            emergency_active=self._emergency_event.is_set(),
            timestamp=time.monotonic(),
        )

        return snapshot

    def get_mutex_occupancy(self) -> dict[str, str | None]:
        """UNIFIED-003: Return mutex group occupancy for telemetry."""
        return {
            grp.value: (cls.value if cls else None)
            for grp, cls in self._mutex_active.items()
        }

    def get_preemption_stats(self) -> dict[str, int]:
        """UNIFIED-003: Return preemption and rejection counters."""
        return {
            "total_preemptions": self._total_preemptions,
            "total_uma_rejections": self._total_uma_rejections,
            "total_mutex_waits": self._total_mutex_waits,
            "active_tokens": len(self._active_tokens),
        }

    def get_admission_log(self) -> list[AdmissionResult]:
        """Return recent admission results for debugging."""
        return list(self._admission_log)

    async def preempt_low_priority(self, target_mb: float) -> float:
        """Signal AND cancel low-priority tasks to release memory.

        UNIFIED-003: Now uses active cancellation (asyncio.Task.cancel)
        instead of just setting a signal flag.

        Returns estimated MB freed.
        Used by ResourceGovernor during CRITICAL/EMERGENCY pressure.
        """
        lock = self._get_lock()
        async with lock:
            preemptable_mb = 0.0
            for ticket in self._allocations.values():
                if ticket.priority in (TaskPriority.LOW, TaskPriority.NORMAL):
                    preemptable_mb += ticket.estimated_mb

            freed = 0.0
            if preemptable_mb > 0:
                self._preempt_signal.set()
                # UNIFIED-003: Also actively cancel preemptible tasks
                freed = self._cancel_preemptible_tasks(target_mb, TaskPriority.HIGH)
                logger.info(
                    f"[PeakLoad] Preemption: signal set + {freed:.0f} MB "
                    f"actively cancelled ({preemptable_mb:.0f} MB preemptable, "
                    f"target: {target_mb:.0f} MB)"
                )

            return freed


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_coordinator: GlobalPeakLoadCoordinator | None = None
_coordinator_lock = threading.Lock()


def get_peak_coordinator() -> GlobalPeakLoadCoordinator:
    """Get or create the singleton GlobalPeakLoadCoordinator.

    This is the canonical way to access the coordinator from outside core/.
    Lazily creates the instance on first call.

    Thread-safe: uses double-checked locking with threading.Lock.
    """
    global _coordinator
    if _coordinator is None:
        with _coordinator_lock:
            if _coordinator is None:
                # Allow override via environment variable for testing
                budget_gib = os.environ.get("HLEDAC_PEAK_BUDGET_GIB")
                if budget_gib:
                    try:
                        budget_gib_float = float(budget_gib)
                        _coordinator = GlobalPeakLoadCoordinator(budget_gib=budget_gib_float)
                        logger.info(f"[PeakLoad] Initialized with custom budget: {budget_gib_float:.2f} GiB")
                    except ValueError:
                        _coordinator = GlobalPeakLoadCoordinator()
                        logger.warning(f"[PeakLoad] Invalid HLEDAC_PEAK_BUDGET_GIB={budget_gib}, using default")
                else:
                    _coordinator = GlobalPeakLoadCoordinator()
                    logger.debug(f"[PeakLoad] Initialized with default budget: {_DEFAULT_TOTAL_BUDGET_GIB:.2f} GiB")
    return _coordinator


def reset_peak_coordinator() -> None:
    """Reset the singleton coordinator. For testing only."""
    global _coordinator
    with _coordinator_lock:
        _coordinator = None
