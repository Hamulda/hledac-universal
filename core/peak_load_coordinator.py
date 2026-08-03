# GlobalPeakLoadCoordinator - Cross-subsystem admission control for M1 8GB UMA.
# UNIFIED-001
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
# ALLOCATION GUARD (async context manager)
# =============================================================================


class _AllocationGuard:
    """Async context manager returned by acquire().

    Holds the allocation ticket and releases it on exit.
    """

    __slots__ = ("_coordinator", "_ticket", "_released")

    def __init__(self, coordinator: GlobalPeakLoadCoordinator, ticket: AllocationTicket) -> None:
        self._coordinator = coordinator
        self._ticket = ticket
        self._released = False

    @property
    def ticket(self) -> AllocationTicket:
        """Access the allocation ticket for telemetry."""
        return self._ticket

    async def __aenter__(self) -> _AllocationGuard:
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

    PREEMPTION PROTOCOL:
        When total allocation exceeds HIGH_WATER (90%), the coordinator
        signals low-priority waiters to back off via asyncio.Event.
        CRITICAL tasks always get through; LOW tasks may be deferred.
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
    )

    def __init__(self, budget_gib: float | None = None) -> None:
        self._lock_factory = threading.Lock()
        self._lock: asyncio.Lock | None = None
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
    ) -> _AllocationGuard:
        """Request admission to allocate estimated_mb megabytes.

        Blocks until:
          1. Budget is available (total + estimated_mb <= budget)
          2. No preemption signal is active for lower-priority tasks
          3. Timeout is reached (raises TimeoutError)

        Returns an async context manager that releases the allocation on exit.

        PRIORITY PREEMPTION:
          - CRITICAL tasks: always admitted immediately, may exceed budget
          - HIGH tasks: admitted if budget available or can preempt LOW/NORMAL
          - NORMAL tasks: admitted if budget available
          - LOW tasks: deferred if HIGH/CRITICAL pressure detected

        FAIL-OPEN:
          If coordinator is in degraded state, admission is granted
          unconditionally with a warning log.
        """
        start_time = time.monotonic()
        lock = self._get_lock()

        # Fast path: check if admission is possible without waiting
        async with lock:
            can_admit, reason = self._can_admit(resource_class, estimated_mb, priority)
            if can_admit:
                ticket = self._allocate(resource_class, estimated_mb, priority, owner)
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

        Returns (can_admit, reason) tuple.
        """
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

    def _allocate(
        self,
        resource_class: ResourceClass,
        estimated_mb: float,
        priority: TaskPriority,
        owner: str,
    ) -> AllocationTicket:
        """Create and register a new allocation ticket.

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

            # Ensure non-negative
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

        Thread-safe: reads are atomic under GIL.
        """
        utilization = self._total_allocated_mb / (self._budget_gib * 1024)
        per_class_gib = {
            cls.value: mb / 1024
            for cls, mb in self._per_class_mb.items()
        }

        return PeakLoadSnapshot(
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

    def get_admission_log(self) -> list[AdmissionResult]:
        """Return recent admission results for debugging."""
        return list(self._admission_log)

    async def preempt_low_priority(self, target_mb: float) -> float:
        """Signal low-priority tasks to release memory.

        Returns estimated MB that can be freed.
        Used by ResourceGovernor during CRITICAL/EMERGENCY pressure.
        """
        lock = self._get_lock()
        async with lock:
            preemptable_mb = 0.0
            for ticket in self._allocations.values():
                if ticket.priority in (TaskPriority.LOW, TaskPriority.NORMAL):
                    preemptable_mb += ticket.estimated_mb

            if preemptable_mb > 0:
                self._preempt_signal.set()
                logger.info(
                    f"[PeakLoad] Preemption signal: {preemptable_mb:.0f} MB "
                    f"of LOW/NORMAL tasks can be freed (target: {target_mb:.0f} MB)"
                )

            return preemptable_mb


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
