"""
Unified Resource Manager — ISSUE-010 Solution
============================================

Centrální správce resource managementu který sjednocuje:
1. ResourceAwareScheduler — task scheduling s resource awareness
2. AIMD Controllers — enrichment, extraction, fetch feedback
3. BackpressureMonitor — UMA memory pressure → concurrency limits
4. MemoryCoordinator — MLX cache + malloc_zone pressure relief

Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │              UnifiedResourceManager                      │
    │  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐  │
    │  │ AIMD Enrich │  │ AIMD Extract│  │ AIMD Fetch    │  │
    │  │ (cpu_bound) │  │ (io_bound) │  │ (network)     │  │
    │  └──────┬──────┘  └──────┬──────┘  └───────┬───────┘  │
    │         │                │                 │           │
    │         └────────────────┼─────────────────┘           │
    │                          ▼                             │
    │               ┌──────────────────┐                   │
    │               │ BackpressureMonitor│                   │
    │               │ (UMA memory state) │                   │
    │               └────────┬─────────┘                   │
    │                        ▼                              │
    │               ┌──────────────────┐                   │
    │               │ ResourceAware    │                   │
    │               │ Scheduler        │                   │
    │               └──────────────────┘                   │
    └─────────────────────────────────────────────────────────┘

M1 8GB UMA optimalizace:
- Memory pressure → automatic scale-down
- Work-stealing rayon pools (cpu, io, mixed)
- Bounded concurrency (never swap)

Usage:
    manager = UnifiedResourceManager()
    await manager.initialize()

    # Schedule with resource constraints
    await manager.schedule_cpu_task(task_id, func, priority=HIGH)
    await manager.schedule_io_task(task_id, func, priority=MEDIUM)

    # Get unified stats
    stats = manager.get_unified_stats()
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any

from hledac.universal.runtime.protocols.cleanup_protocol import shutdown_aclose
from hledac.universal.coordinators.aimd_controllers import (
    AIMDController,
    make_enrich_aimd,
    make_extract_aimd,
    make_fetch_aimd,
)

if TYPE_CHECKING:
    from hledac.universal.coordinators.backpressure import BackpressureMonitor
    from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator
    from hledac.universal.coordinators.resource_allocator import IntelligentResourceAllocator

# E4: OTel trace context propagation on I/O boundaries
from hledac.universal.utils.async_helpers import safe_create_task  # noqa: E402

logger = logging.getLogger(__name__)

# M1 8GB UMA bounds
_MAX_ENRICH_WORKERS = 16
_MAX_EXTRACT_WORKERS = 8
_MAX_FETCH_WORKERS = 25
_MEMORY_PRESSURE_SCALE_DOWN = 0.5  # Reduce workers by 50% on memory pressure


class TaskRequest(msgspec.Struct):
    """Resource request for a task."""
    task_id: str
    task_name: str
    priority: int  # 1-5 (LOW to EMERGENCY)
    task_type: str  # 'cpu', 'io', 'fetch'
    estimated_duration: float = 60.0
    can_preempt: bool = True


class UnifiedResourceManager:
    """
    Centralizovaný resource manager.

    Sjednocuje:
    - IntelligentResourceAllocator (task scheduling)
    - AIMD controllers (concurrency feedback)
    - BackpressureMonitor (memory-pressure-driven limits)
    - MemoryCoordinator (MLX cache + malloc_zone)

    Výhody:
    - Single source of truth pro limity
    - Unified telemetry
    - Memory-aware scheduling
    - Automatic scale-down na M1 8GB
    """
    __slots__ = tuple((
        '_allocator', '_backpressure', '_memory_coordinator',
        '_enrich_controller', '_extract_controller', '_fetch_controller',
        '_active_tasks', '_lock', '_initialized', '_shutdown',
    ))

    def __init__(
        self,
        allocator: IntelligentResourceAllocator | None = None,
        backpressure: BackpressureMonitor | None = None,
        memory_coordinator: UniversalMemoryCoordinator | None = None,
    ) -> None:
        self._allocator = allocator
        self._backpressure = backpressure
        self._memory_coordinator = memory_coordinator

        # AIMD controllers
        self._enrich_controller = make_enrich_aimd()
        self._extract_controller = make_extract_aimd()
        self._fetch_controller = make_fetch_aimd()

        self._active_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self._shutdown = False

    async def initialize(self) -> None:
        """Initialize the unified manager."""
        if self._initialized:
            return

        # Lazy imports
        if self._allocator is None:
            try:
                from hledac.universal.coordinators.resource_allocator import (
                    IntelligentResourceAllocator,
                )
                self._allocator = IntelligentResourceAllocator()
                logger.info("[UnifiedResourceManager] Allocator initialized")
            except ImportError as e:
                logger.warning(f"[UnifiedResourceManager] Allocator unavailable: {e}")

        if self._backpressure is None:
            try:
                from hledac.universal.core.resource_governor import M1ResourceGovernor
                governor = M1ResourceGovernor()
                from hledac.universal.coordinators.backpressure import BackpressureMonitor
                self._backpressure = BackpressureMonitor(governor)
                logger.info("[UnifiedResourceManager] Backpressure initialized")
            except ImportError as e:
                logger.warning(f"[UnifiedResourceManager] Backpressure unavailable: {e}")

        if self._memory_coordinator is None:
            try:
                from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator
                self._memory_coordinator = UniversalMemoryCoordinator()
                logger.info("[UnifiedResourceManager] MemoryCoordinator initialized")
            except ImportError as e:
                logger.warning(f"[UnifiedResourceManager] MemoryCoordinator unavailable: {e}")

        self._initialized = True
        logger.info("[UnifiedResourceManager] Initialized")

    # -------------------------------------------------------------------------
    # Unified concurrency limits (combines AIMD + Backpressure)
    # -------------------------------------------------------------------------

    def _get_effective_limit(self, task_type: str) -> int:
        """
        Vrací efektivní limit pro daný task_type.

        Kombinuje:
        1. AIMD controller window (base limit)
        2. Backpressure memory state (scale factor)
        """
        # Get AIMD window
        if task_type == 'cpu':
            controller = self._enrich_controller
            max_limit = _MAX_ENRICH_WORKERS
        elif task_type == 'io':
            controller = self._extract_controller
            max_limit = _MAX_EXTRACT_WORKERS
        else:  # fetch
            controller = self._fetch_controller
            max_limit = _MAX_FETCH_WORKERS

        aimd_window = controller.window

        # Apply backpressure scale
        scale = 1.0
        if self._backpressure is not None:
            decision = self._backpressure.get_decision()
            if decision.uma_state == 'soft_warn':
                scale = 0.75
            elif decision.uma_state == 'warn':
                scale = 0.5
            elif decision.uma_state in ('critical', 'emergency'):
                scale = 0.25

        effective = min(int(aimd_window * scale), max_limit)
        return max(1, effective)

    async def can_schedule(self, request: TaskRequest) -> bool:
        """
        Zkontroluje zda lze task naplánovat.

        Returns:
            True pokud máme kapacitu
        """
        effective_limit = self._get_effective_limit(request.task_type)
        active_count = sum(
            1 for t in self._active_tasks.values()
            if not t.done()
        )
        return active_count < effective_limit

    async def schedule(
        self,
        request: TaskRequest,
        coro: Awaitable,
    ) -> bool:
        """
        Naplánuje task s resource awareness.

        Args:
            request: TaskRequest s prioritou a typem
            coro: Coroutine k execute

        Returns:
            True pokud úspěšně naplánováno
        """
        if self._shutdown:
            return False

        if not await self.can_schedule(request):
            return False

        effective_limit = self._get_effective_limit(request.task_type)

        # Wait for slot if at capacity
        while True:
            active_count = sum(1 for t in self._active_tasks.values() if not t.done())
            if active_count < effective_limit:
                break
            await asyncio.sleep(0)

        async with self._lock:
            # E4: safe_create_task propagates OTel trace context into child task
            task = safe_create_task(self._run_task(request, coro), name=f"unified_rm:{request.task_type}:{request.task_id}")
            self._active_tasks[request.task_id] = task

            def done_callback(t: asyncio.Task) -> None:
                self._active_tasks.pop(request.task_id, None)
                # AIMD feedback - check if cancelled or has exception
                exc = t.exception()
                if exc is None and not t.cancelled():
                    self._on_success(request.task_type)
                else:
                    self._on_failure(request.task_type)

            task.add_done_callback(done_callback)
            return True

    async def _run_task(self, request: TaskRequest, coro: Awaitable) -> None:
        """Execute task a handle exceptions."""
        try:
            await coro
        except asyncio.CancelledError:
            logger.info(f"[UnifiedResourceManager] Task {request.task_id} cancelled")
            raise
        except Exception as e:
            logger.error(f"[UnifiedResourceManager] Task {request.task_id} failed: {e}")

    def _on_success(self, task_type: str) -> None:
        """AIMD success feedback."""
        # F350M-R ISSUE #31: safe_create_task with eager_start=True (AIMD feedback is hot path)
        if task_type == 'cpu':
            safe_create_task(self._enrich_controller.on_success(), eager_start=True)
        elif task_type == 'io':
            safe_create_task(self._extract_controller.on_success(), eager_start=True)
        else:
            safe_create_task(self._fetch_controller.on_success(), eager_start=True)

    def _on_failure(self, task_type: str) -> None:
        """AIMD failure feedback."""
        # F350M-R ISSUE #31: safe_create_task with eager_start=True (AIMD feedback is hot path)
        if task_type == 'cpu':
            safe_create_task(self._enrich_controller.on_failure(), eager_start=True)
        elif task_type == 'io':
            safe_create_task(self._extract_controller.on_failure(), eager_start=True)
        else:
            safe_create_task(self._fetch_controller.on_failure(), eager_start=True)

    # -------------------------------------------------------------------------
    # Convenience methods
    # -------------------------------------------------------------------------

    async def schedule_cpu_task(
        self,
        task_id: str,
        coro: Awaitable,
        priority: int = 3,
    ) -> bool:
        """Schedule CPU-bound task."""
        request = TaskRequest(
            task_id=task_id,
            task_name=task_id,
            priority=priority,
            task_type='cpu',
        )
        return await self.schedule(request, coro)

    async def schedule_io_task(
        self,
        task_id: str,
        coro: Awaitable,
        priority: int = 2,
    ) -> bool:
        """Schedule I/O-bound task."""
        request = TaskRequest(
            task_id=task_id,
            task_name=task_id,
            priority=priority,
            task_type='io',
        )
        return await self.schedule(request, coro)

    async def schedule_fetch_task(
        self,
        task_id: str,
        coro: Awaitable,
        priority: int = 2,
    ) -> bool:
        """Schedule network fetch task."""
        request = TaskRequest(
            task_id=task_id,
            task_name=task_id,
            priority=priority,
            task_type='fetch',
        )
        return await self.schedule(request, coro)

    # -------------------------------------------------------------------------
    # Stats & shutdown
    # -------------------------------------------------------------------------

    def get_unified_stats(self) -> dict[str, Any]:
        """Vrátí unified statistiky všech subsystémů."""
        active_count = sum(1 for t in self._active_tasks.values() if not t.done())

        stats: dict[str, Any] = {
            'active_tasks': active_count,
            'initialized': self._initialized,
            'shutdown': self._shutdown,
            'aimd': {
                'enrich': {
                    'window': self._enrich_controller.window,
                    'successes': self._enrich_controller.successes,
                    'failures': self._enrich_controller.failures,
                },
                'extract': {
                    'window': self._extract_controller.window,
                    'successes': self._extract_controller.successes,
                    'failures': self._extract_controller.failures,
                },
                'fetch': {
                    'window': self._fetch_controller.window,
                    'successes': self._fetch_controller.successes,
                    'failures': self._fetch_controller.failures,
                },
            },
            'limits': {
                'cpu': self._get_effective_limit('cpu'),
                'io': self._get_effective_limit('io'),
                'fetch': self._get_effective_limit('fetch'),
            },
        }

        if self._backpressure is not None:
            decision = self._backpressure.get_decision()
            stats['backpressure'] = {
                'uma_state': decision.uma_state,
                'clearnet_max': decision.clearnet_max,
                'stealth_max': decision.stealth_max,
                'io_only': decision.io_only,
            }

        if self._memory_coordinator is not None:
            try:
                stats['memory'] = {
                    'pressure_level': getattr(self._memory_coordinator, '_pressure_level', 'unknown'),
                }
            except Exception:
                pass

        return stats

    # P1-9: Canonical aclose timeout — matches DEFAULT_ACLOSE_TIMEOUT_S.
    DEFAULT_TIMEOUT_S = 10.0

    async def shutdown(self, timeout: float = 30.0) -> None:
        """P1-9: Graceful shutdown with force-shutdown fallback."""
        if self._shutdown:
            return
        await shutdown_aclose(
            name="UnifiedResourceManager",
            coro=self._do_shutdown(),
            timeout_s=timeout,
        )

    async def _do_shutdown(self) -> None:
        """Inner cleanup — called by shutdown() via shutdown_aclose()."""
        async with self._lock:
            self._shutdown = True

        # Cancel active tasks
        if self._active_tasks:
            logger.info(f"[UnifiedResourceManager] Cancelling {len(self._active_tasks)} tasks")
            for task in self._active_tasks.values():
                task.cancel()

            # Wait with timeout
            if self._active_tasks:
                await asyncio.wait(
                    self._active_tasks.values(),
                    timeout=30.0,
                    return_when=asyncio.ALL_COMPLETED,
                )

        # UniversalMemoryCoordinator doesn't require async shutdown
        logger.info("[UnifiedResourceManager] Shutdown complete")


# Global singleton
_unified_resource_manager: UnifiedResourceManager | None = None


def get_unified_resource_manager() -> UnifiedResourceManager:
    """Get or create the global UnifiedResourceManager instance."""
    global _unified_resource_manager
    if _unified_resource_manager is None:
        _unified_resource_manager = UnifiedResourceManager()
    return _unified_resource_manager
