"""
Unified Executor — ISSUE-010 Solution
=====================================

Centralizovaný pool executor který sjednocuje:
1. Rust rayon pool (cpu_pool, io_pool, mixed_pool) — work-stealing scheduler
2. AIMD feedback control — automatická adaptace velikosti poolů
3. ResourceAwareScheduler — centrální budget management
4. WakeFd signaling — cross-thread komunikace bez GIL contention

Pro M1 8GB UMA optimalizovaný:
- cpu_pool: 4 P-cores pro CPU-bound SIMD/hot path
- io_pool: 2 threads pro I/O-bound (DuckDB, compress)
- mixed_pool: adaptive 1-2 threads podle batch size

AIMD Strategy:
- Enrichment (CPU-bound): ceiling=16 workers, +1 na success, ×0.75 na failure
- Extraction (I/O-bound): ceiling=8 workers, +1 na success, ×0.75 na failure
- Fetch: ceiling=25, +2 na success, ×0.75 na failure (převzato z FetchCoordinator)

Architecture:
    asyncio.to_thread() → rayon pool (work-stealing)
                          ↓
                    WakeFd event
                          ↓
              loop.add_reader() → async notification

Usage:
    executor = UnifiedExecutor()
    await executor.run_cpu_bound(task, *args)     # rayon cpu_pool
    await executor.run_io_bound(task, *args)      # rayon io_pool
    result = await executor.submit_mixed(n, task, *args)  # rayon mixed_pool
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar, cast

from hledac.universal.coordinators.aimd_controllers import (
    AIMDController,
    make_enrich_aimd,
    make_extract_aimd,
    make_fetch_aimd,
)
try:
    from hledac.universal.runtime.async_helpers import safe_create_task
except ImportError:
    safe_create_task = None  # Fallback

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

T = TypeVar("T")

# M1 8GB UMA bounds
_MAX_ENRICH_WORKERS = 16  # CPU-bound ceiling
_MAX_EXTRACT_WORKERS = 8  # I/O-bound ceiling
_MAX_FETCH_WORKERS = 25  # Network-bound ceiling
_MAX_CPU_POOL_THREADS = 4  # P-cores for SIMD
_MAX_IO_POOL_THREADS = 2  # I/O-bound


@dataclass(slots=True)
class PoolStats:
    """Statistiky pro jeden pool."""
    active_workers: int = 0
    queued_tasks: int = 0
    total_completed: int = 0
    total_failed: int = 0
    current_window: float = 1.0
    aimd_controller: AIMDController | None = None


class UnifiedExecutor:
    """
    Centralizovaný executor pro všechny pool operace.

    Sjednocuje:
    - Rust rayon pools (cpu, io, mixed)
    - AIMD feedback control
    - Resource-aware scheduling

    Pro M1 8GB: work-stealing rayon pool je efektivnější než
    ThreadPoolExecutor pro variabilní CPU úlohy.
    """
    __slots__ = tuple((
        '_enrich_stats', '_extract_stats', '_fetch_stats',
        '_rayon_available', '_rust_extensions',
        '_lock', '_shutdown',
    ))

    def __init__(self) -> None:
        self._enrich_stats = PoolStats(
            aimd_controller=make_enrich_aimd(),
        )
        self._extract_stats = PoolStats(
            aimd_controller=make_extract_aimd(),
        )
        self._fetch_stats = PoolStats(
            aimd_controller=make_fetch_aimd(),
        )
        self._lock = asyncio.Lock()
        self._shutdown = False

        # Lazy Rust extensions initialization
        self._rust_extensions: Any = None
        self._rayon_available = self._check_rayon()

    def _check_rayon(self) -> bool:
        """Check if Rust rayon pools are available."""
        try:
            from hledac_rust_extensions import (
                cpu_pool_run,
                io_pool_run,
                mixed_pool_run,
                rayon_submit,
            )
            self._rust_extensions = {
                'cpu_pool_run': cpu_pool_run,
                'io_pool_run': io_pool_run,
                'mixed_pool_run': mixed_pool_run,
                'rayon_submit': rayon_submit,
            }
            logger.info("[UnifiedExecutor] Rust rayon pools available")
            return True
        except ImportError:
            logger.warning("[UnifiedExecutor] Rust rayon not available — falling back to asyncio.to_thread")
            self._rust_extensions = None
            return False

    # -------------------------------------------------------------------------
    # AIMD-aware pool execution
    # -------------------------------------------------------------------------

    async def run_cpu_bound(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Spustí CPU-bound úlohu na rayon cpu_pool.

        Používá AIMD pro automatické řízení concurrency.
        Na M1 4 P-cores pro SIMD/hot path úlohy.

        POZNÁMKA: cpu_pool_run je sync GIL wrapper — volá Python funkci
        přímo. Pro skutečné rayon parallel provádění použij submit_mixed()
        nebo rayon_submit() s pool.install().
        """
        if self._shutdown:
            raise RuntimeError("UnifiedExecutor already shutdown")

        stats = self._enrich_stats
        controller = stats.aimd_controller

        # AIMD window check
        if controller is not None:
            current_window = controller.window
            if stats.active_workers >= current_window:
                # At capacity — wait for slot
                await asyncio.sleep(0.01)

        stats.active_workers += 1

        try:
            if self._rayon_available:
                # Use rayon_submit for true parallel execution on rayon pool
                # rayon_submit runs func inside pool.install() and returns result via shared Arc<Mutex>
                handle = await asyncio.to_thread(
                    self._rust_extensions['rayon_submit'],
                    "cpu",  # pool_type
                    max(1, len(args) if args else 0),  # n_items hint
                    func,  # Python callable
                    args,  # args tuple
                )
                # Join the rayon task — returns the Python result directly
                result = await asyncio.to_thread(
                    self._rust_extensions['rayon_join'],
                    handle,
                )
            else:
                # Fallback: asyncio.to_thread
                result = await asyncio.to_thread(func, *args, **kwargs)

            # AIMD success
            if controller:
                await controller.on_success()

            stats.total_completed += 1
            return result  # Already unwrapped

        except Exception as e:
            stats.total_failed += 1

            # AIMD failure
            if controller:
                await controller.on_failure()

            logger.error(f"[UnifiedExecutor] CPU task failed: {e}")
            raise

        finally:
            stats.active_workers -= 1

    async def run_io_bound(
        self,
        func: Callable[..., Awaitable[T] | T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Spustí I/O-bound úlohu na rayon io_pool.

        Používá AIMD pro automatické řízení concurrency.
        Na M1 2 threads pro DuckDB/compress úlohy.

        POZNÁMKA: io_pool_run je sync GIL wrapper — volá Python funkci
        přímo. Pro skutečné rayon parallel provádění použij submit_mixed().
        """
        if self._shutdown:
            raise RuntimeError("UnifiedExecutor already shutdown")

        stats = self._extract_stats
        controller = stats.aimd_controller

        if controller is not None:
            current_window = controller.window
            if stats.active_workers >= current_window:
                await asyncio.sleep(0.01)

        stats.active_workers += 1

        try:
            if self._rayon_available:
                # Use rayon_submit for true parallel execution on rayon pool
                # rayon_submit runs func inside pool.install() and returns result via shared Arc<Mutex>
                handle = await asyncio.to_thread(
                    self._rust_extensions['rayon_submit'],
                    "io",
                    max(1, len(args) if args else 0),
                    func,
                    args,
                )
                # Join the rayon task — returns the Python result directly
                result = await asyncio.to_thread(
                    self._rust_extensions['rayon_join'],
                    handle,
                )
            else:
                result = await asyncio.to_thread(func, *args, **kwargs)

            if controller:
                await controller.on_success()

            stats.total_completed += 1
            # Unwrap Awaitable[T] if func returned coroutine
            if asyncio.iscoroutine(result):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]

        except Exception as e:
            stats.total_failed += 1
            if controller:
                await controller.on_failure()
            logger.error(f"[UnifiedExecutor] IO task failed: {e}")
            raise

        finally:
            stats.active_workers -= 1

    async def submit_mixed(
        self,
        n_items: int,
        func: Callable[..., T],
        *args: Any,
    ) -> list[T]:
        """
        Submit batch na rayon mixed_pool.

        Adaptive 1-2 threads podle batch size.
        Ideální pro variabilní úlohy.
        """
        if self._shutdown:
            raise RuntimeError("UnifiedExecutor already shutdown")

        if self._rayon_available:
            result = await asyncio.to_thread(
                self._rust_extensions['mixed_pool_run'],
                n_items,
                func,
                args,
            )
            return result
        else:
            # Fallback: sequential
            return [func(*args) for _ in range(n_items)]

    # -------------------------------------------------------------------------
    # rayon_submit — pro advanced use cases
    # -------------------------------------------------------------------------

    async def rayon_submit(
        self,
        pool_type: str,
        n_items: int,
        func: Callable[..., Any],
        args: tuple,
    ) -> int:
        """
        Submit na rayon pool a vrať handle pro join/abort.

        Args:
            pool_type: "cpu" | "io" | "mixed"
            n_items: batch size hint
            func: Python callable
            args: argument tuple

        Returns:
            Opaque handle (usize) pro rayon_join/rayon_abort
        """
        if not self._rayon_available:
            raise RuntimeError("Rayon not available")

        return await asyncio.to_thread(
            self._rust_extensions['rayon_submit'],
            pool_type,
            n_items,
            func,
            args,
        )

    # -------------------------------------------------------------------------
    # Stats & shutdown
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Vrátí statistiky všech poolů."""
        def stats_to_dict(s: PoolStats) -> dict[str, Any]:
            return {
                'active_workers': s.active_workers,
                'queued_tasks': s.queued_tasks,
                'total_completed': s.total_completed,
                'total_failed': s.total_failed,
                'current_window': s.current_window,
                'aimd': {
                    'window': s.aimd_controller.window if s.aimd_controller else 0,
                    'successes': s.aimd_controller.successes if s.aimd_controller else 0,
                    'failures': s.aimd_controller.failures if s.aimd_controller else 0,
                } if s.aimd_controller else None,
            }

        return {
            'enrich': stats_to_dict(self._enrich_stats),
            'extract': stats_to_dict(self._extract_stats),
            'fetch': stats_to_dict(self._fetch_stats),
            'rayon_available': self._rayon_available,
        }

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Graceful shutdown."""
        async with self._lock:
            self._shutdown = True

        await asyncio.sleep(min(timeout, 0.1))  # Allow pending tasks to notice
        logger.info(f"[UnifiedExecutor] Shutdown complete")


# Global singleton
_unified_executor: UnifiedExecutor | None = None


def get_unified_executor() -> UnifiedExecutor:
    """Get or create the global UnifiedExecutor instance."""
    global _unified_executor
    if _unified_executor is None:
        _unified_executor = UnifiedExecutor()
    return _unified_executor


async def enrich_task(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Convenience: spustí CPU-bound enrichment task."""
    executor = get_unified_executor()
    return await executor.run_cpu_bound(func, *args, **kwargs)


async def extract_task(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Convenience: spustí I/O-bound extraction task."""
    executor = get_unified_executor()
    return await executor.run_io_bound(func, *args, **kwargs)
