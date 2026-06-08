"""
Async Utilities - Bounded Concurrency Helpers
=============================================

Sprint 81, Fáze 2: Performance Wins & Concurrency

Poskytuje bounded concurrency nástroje:
- bounded_map: spouští úlohy s omezenou concurrency
- map_as_completed: průběžně vrací výsledky dle as_completed
- TaskResult: strukturovaný výsledek s indexem pro zachování mapování

Features:
- BoundedSemaphore pro limitování paralelních úloh
- Retry s exponenciálním backoff a jitter
- Memory-aware: při vysokém memory pressure se sníží concurrency
- Index-mapping: zachování pořadí vstup→výstup i při dílčích chybách
- Python 3.11+ TaskGroup support pro cancel_on_error

Example:
    tasks = [
        (fetch_url, ("https://example.com",), {}),
        (parse_html, (html_content,), {}),
    ]
    results = await bounded_map(tasks, max_concurrent=3, max_retries=2)
"""

from __future__ import annotations

import asyncio
import logging
import random
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from .async_helpers import safe_gather_dropin, safe_gather_strict
logger = logging.getLogger(__name__)

T = TypeVar('T')


class TaskResult:
    """Výsledek úlohy s indexem pro zachování mapování."""

    def __init__(self, index: int, value: T | None, error: Exception | None = None):
        self.index = index
        self.value = value
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:
        if self.success:
            return f"TaskResult({self.index}, success)"
        return f"TaskResult({self.index}, error={self.error})"


# Memory monitor import - fail-safe
_UnifiedMemoryMonitor = None
try:
    from .memory_dashboard import UnifiedMemoryMonitor as _UnifiedMemoryMonitor
except ImportError:
    pass


def _get_memory_level() -> float:
    """Get current memory pressure level (0.0-1.0)."""
    if _UnifiedMemoryMonitor is not None:
        try:
            monitor = _UnifiedMemoryMonitor()
            snap = monitor.snapshot()
            return snap.pressure
        except Exception:
            pass
    return 0.0


async def bounded_map[T](
    tasks: list[tuple[Callable[..., Awaitable[T]], tuple, dict]],
    max_concurrent: int = 3,
    max_retries: int = 0,
    cancel_on_error: bool = True,
    memory_pressure_check: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    timeout: float | None = None
) -> list[T | None]:
    """
    Spouští úlohy s omezenou concurrency.

    Args:
        tasks: seznam (fn, args, kwargs)
        max_concurrent: max paralelních úloh
        max_retries: počet opakování při selhání
        cancel_on_error: True → TaskGroup (3.11+), jinak gather; False → vždy gather
        memory_pressure_check: pokud True a memory >85%, sníží concurrency
        retryable_exceptions: které typy výjimek opakovat
        timeout: timeout pro jednotlivé volání

    Returns:
        Seznam stejné délky jako vstup. Úspěšné výsledky na odpovídajících indexech,
        selhané jako None (cancel_on_error=False) nebo chyba se propaguje (cancel_on_error=True).
    """
    if memory_pressure_check:
        mem_level = _get_memory_level()
        if mem_level > 0.85:
            max_concurrent = min(max_concurrent, 2)
            logger.warning("Memory pressure high (%.1f%%), reducing concurrency to %d",
                          mem_level * 100, max_concurrent)

    sem = asyncio.BoundedSemaphore(max_concurrent)

    async def _run(index: int, fn: Callable[..., Awaitable[T]], args: tuple, kwargs: dict) -> T | None:
        for attempt in range(max_retries + 1):
            try:
                async with sem:
                    if timeout is not None:
                        # asyncio.timeout (3.11+) — preferred over wait_for:
                        # better cancellation semantics, less Python overhead (M1 8GB UMA).
                        async with asyncio.timeout(timeout):
                            return await fn(*args, **kwargs)
                    else:
                        return await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt == max_retries or not isinstance(e, retryable_exceptions):
                    raise
                # Jitter: random 0.5-1.5 × exponenciální backoff
                delay = 0.5 * (2 ** attempt) * random.uniform(0.5, 1.5)
                logger.debug(f"Task {index} retry {attempt + 1}/{max_retries} after {delay:.2f}s")
                await asyncio.sleep(delay)
        return None

    results: list[T | None] = [None] * len(tasks)

    if sys.version_info >= (3, 11) and cancel_on_error:
        # F262D: standardized asyncio.TaskGroup → safe_gather_strict
        # (PEP 654 / 3.11+ TaskGroup-based, all-or-nothing preserved)
        coros = [_run(i, fn, a, k) for i, (fn, a, k) in enumerate(tasks)]
        return await safe_gather_strict(*coros, label=f"async_utils:run[{len(tasks)}]")

    # Python < 3.11 nebo cancel_on_error=False
    coros = [_run(i, fn, a, k) for i, (fn, a, k) in enumerate(tasks)]
    gathered = await safe_gather_dropin(*coros, label="async_utils:149")

    if cancel_on_error:
        for res in gathered:
            if isinstance(res, BaseException):
                raise res
        return gathered
    else:
        for i, res in enumerate(gathered):
            results[i] = None if isinstance(res, BaseException) else res
        return results


async def map_as_completed[T](
    tasks: list[tuple[Callable[..., Awaitable[T]], tuple, dict]],
    max_concurrent: int = 3,
    **kwargs
) -> AsyncIterator[tuple[int, T]]:
    """
    Průběžně vrací výsledky dle as_completed, index zachován.
    Užitečné pro OSINT fetching – dostáváme findings postupně.

    Args:
        tasks: seznam (fn, args, kwargs)
        max_concurrent: max paralelních úloh
        **kwargs: další argumenty pro bounded_map

    Yields:
        (index, result) tuple - výsledky jakmile jsou hotové
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=max_concurrent * 2)  # C2: bounded to prevent unbounded memory growth
    sem = asyncio.Semaphore(max_concurrent)

    async def _worker(idx: int, fn: Callable[..., Awaitable[T]], args: tuple, kw: dict):
        async with sem:
            try:
                result = await fn(*args, **kw)
                await q.put((idx, result, None))
            except Exception as e:
                await q.put((idx, None, e))

    # Start all tasks
    for i, (fn, args, kw) in enumerate(tasks):
        asyncio.create_task(_worker(i, fn, args, kw), name=f"async_utils:map-{i}")

    remaining = len(tasks)
    while remaining > 0:
        idx, val, err = await q.get()
        remaining -= 1
        if err is not None:
            # For streaming, we log and continue
            logger.warning(f"Task {idx} failed: {err}")
            continue
        yield idx, val


async def bounded_gather[T](
    *coros: Awaitable[T],
    max_concurrent: int = 3,
    return_exceptions: bool = False,
    per_task_timeout: float | None = None
) -> list[T]:
    """
    Jednodušší wrapper pro bounded gather s per-task timeout (asyncio.timeout).

    Args:
        *coros: coroutines to gather
        max_concurrent: max paralelních úloh
        return_exceptions: pokud True, chyby se vrátí jako výsledky místo raised
        per_task_timeout: timeout pro jednotlivé coroutine (asyncio.timeout, Python 3.11+)

    Returns:
        Seznam výsledků. Při return_exceptions=True mohou být na indexech výjimky
        (včetně TimeoutError z per_task_timeout).

    Notes:
        - Používá asyncio.gather + asyncio.Semaphore (ne TaskGroup) aby zachoval
          return_exceptions=True sémantiku. TaskGroup vždy canceluje siblings.
        - S per_task_timeout=None je chování identické s bounded_map (ale bez
          return_exceptions=True bugu v bounded_map; bounded_gather je preferované).
    """
    if not coros:
        return []

    sem = asyncio.Semaphore(max_concurrent)

    async def _run(coro: Awaitable[T]) -> T:
        async with sem:
            if per_task_timeout is not None:
                async with asyncio.timeout(per_task_timeout):
                    return await coro
            return await coro

    return await safe_gather_dropin(*(_run(c) for c in coros), label="async_utils:242")  # type: ignore[return-value]


__all__ = [
    'TaskResult',
    'bounded_map',
    'map_as_completed',
    'bounded_gather',
]
