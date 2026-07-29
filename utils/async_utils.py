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
import asyncio
import logging
import secrets
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, TypeVar, cast
from .async_helpers import parallel_ok, parallel
logger = logging.getLogger(__name__)
_JITTER_RNG = secrets.SystemRandom()
T = TypeVar('T', default=Any)

class TaskResult:
    """Výsledek úlohy s indexem pro zachování mapování."""
    __slots__ = tuple(('error', 'index', 'value'))

    def __init__(self, index: int, value: T | None, error: Exception | None=None):
        self.index = index
        self.value = value
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:
        if self.success:
            return f'TaskResult({self.index}, success)'
        return f'TaskResult({self.index}, error={self.error})'
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

async def bounded_map[T](tasks: Sequence[tuple[Callable[..., Awaitable[T]], tuple, dict]], max_concurrent: int=3, max_retries: int=0, cancel_on_error: bool=True, memory_pressure_check: bool=True, retryable_exceptions: tuple[type[Exception], ...]=(Exception,), timeout: float | None=None) -> list[T | None]:
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
            logger.warning('Memory pressure high (%.1f%%), reducing concurrency to %d', mem_level * 100, max_concurrent)
    sem = asyncio.BoundedSemaphore(max_concurrent)

    async def _run(index: int, fn: Callable[..., Awaitable[T]], args: tuple, kwargs: dict) -> T | None:
        for attempt in range(max_retries + 1):
            try:
                async with sem:
                    if timeout is not None:
                        async with asyncio.timeout(timeout):
                            return await fn(*args, **kwargs)
                    else:
                        return await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt == max_retries or not isinstance(e, retryable_exceptions):
                    raise
                delay = 0.5 * 2 ** attempt * _JITTER_RNG.uniform(0.5, 1.5)
                logger.debug(f'Task {index} retry {attempt + 1}/{max_retries} after {delay:.2f}s')
                await asyncio.sleep(delay)
        return None
    results: list[T | None] = [None] * len(tasks)
    if sys.version_info >= (3, 11) and cancel_on_error:
        coros = [_run(i, fn, a, k) for i, (fn, a, k) in enumerate(tasks)]
        result = await parallel(coros, taskgroup=True, policy='raise', ctx=f'async_utils:run[{len(tasks)}]')
        return result.ok
    coros = [_run(i, fn, a, k) for i, (fn, a, k) in enumerate(tasks)]
    gathered = await parallel_ok(*coros, label='async_utils:149')
    if cancel_on_error:
        for res in gathered:
            if isinstance(res, BaseException):
                raise res
        return gathered
    else:
        for i, res in enumerate(gathered):
            if isinstance(res, BaseException):
                results[i] = None
            else:
                results[i] = cast(T, res)
        return results

async def map_as_completed[T](tasks: Sequence[tuple[Callable[..., Awaitable[T]], tuple, dict]], max_concurrent: int=3, **kwargs) -> AsyncIterator[tuple[int, T]]:
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
    q: asyncio.Queue = asyncio.Queue(maxsize=max_concurrent * 2)
    sem = asyncio.Semaphore(max_concurrent)

    async def _worker(idx: int, fn: Callable[..., Awaitable[T]], args: tuple, kw: dict):
        async with sem:
            try:
                result = await fn(*args, **kw)
                await q.put((idx, result, None))
            except Exception as e:
                await q.put((idx, None, e))
    for i, (fn, args, kw) in enumerate(tasks):
        safe_create_task(_worker(i, fn, args, kw), name=f'async_utils:map-{i}')
    remaining = len(tasks)
    while remaining > 0:
        idx, val, err = await q.get()
        remaining -= 1
        if err is not None:
            logger.warning(f'Task {idx} failed: {err}')
            continue
        yield (idx, val)

async def bounded_gather[T](*coros: Awaitable[T], max_concurrent: int=3, return_exceptions: bool=False, per_task_timeout: float | None=None) -> list[T]:
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
    return await parallel_ok(*(_run(c) for c in coros), label='async_utils:242')

class BoundedTaskSet:
    """
    asyncio.Task registry s bound na počet concurrent úloh.

    Fix K11/F3.3: `_bg_tasks` byl unbound ``set[asyncio.Task]`` —
    při burstu mohla sada narůst neomezeně (např. 500+ tasků při
    mass drain z duckdb_store). Třída nahrazuje přímý ``set`` všude,
    kde se trackují background tasks.

    Features:
    - Semaphore-bound spawning (default 256)
    - Auto-cancel všech pending tasks při .cancel()
    - Auto-cleanup via done_callback
    - Exception logging na každém dokončeném tasku
    - Fail-open: žádné operation nepustí exception ven

    M1 8GB: maxsize=256 je ceiling; typický steady-state << 32.

    DEPRECATED (F320-B4): Python 3.11+ asyncio.TaskGroup je preferovaná
    alternativa. Tato třída zůstává funkční pro:
    - duckdb_store.py kde je vázána na __slots__ a nelze snadno změnit
    - sync kontexty které potřebují async spawn (require loop running)
    Do NOT nových nasazení — použij TaskGroup s `async with` context manager.

    Usage:
        ts = BoundedTaskSet(maxsize=256)
        t = await ts.spawn(my_coro(), name="fetch:example.com")
        await ts.cancel()  # broadcast cancel + drain
    """
    __slots__ = tuple(('_cancel_requested', '_lock', '_maxsize', '_sem', '_tasks'))

    def __init__(self, maxsize: int=256) -> None:
        self._maxsize = maxsize
        self._tasks: dict[asyncio.Task, str] = {}
        self._sem = asyncio.Semaphore(maxsize)
        self._cancel_requested = False
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        """Počet active (nedokončených) tasks."""
        return len(self._tasks)

    async def spawn(self, coro: Awaitable[Any], name: str | None=None) -> asyncio.Task:
        """
        Vytvoří a registeruje task — blokuje pokud `maxsize` reached.

        Args:
            coro: coroutine k exekuci
            name: volitelné jméno tasku (pro debugging/logging)

        Returns:
            asyncio.Task instance
        """
        if self._cancel_requested:
            t = asyncio.current_task()
            if t is not None:
                return t
            t = safe_create_task(asyncio.sleep(0))
            t.cancel()
            return t
        await self._sem.acquire()
        task = safe_create_task(cast(Any, coro), name=name or 'bounded_taskset:anon')
        task_name = task.get_name()
        async with self._lock:
            self._tasks[task] = task_name

        def _done_callback(f: asyncio.Task) -> None:
            self._tasks.pop(f, None)
            self._sem.release()
            try:
                if not f.cancelled():
                    exc = f.exception()
                    if exc is not None:
                        logger.warning(f'[BoundedTaskSet] Task {f.get_name()} failed: {exc!r}')
            except asyncio.InvalidStateError:
                pass
        task.add_done_callback(_done_callback)
        return task

    async def cancel(self) -> None:
        """
        Cancel VŠECHNY pending tasks a počkat na jejich dokončení.

        Bezpecná proti re-entry: cancel() lze volat vícekrát.
        """
        self._cancel_requested = True
        async with self._lock:
            tasks = list(self._tasks.keys())
        if not tasks:
            return
        logger.debug(f'[BoundedTaskSet] Cancelling {len(tasks)} tasks')
        for t in tasks:
            t.cancel()
        await parallel(tasks, taskgroup=True, policy='log', ctx='async_utils:BoundedTaskSet:cancel', logger_instance=logger)
        async with self._lock:
            self._tasks.clear()
__all__ = ['TaskResult', 'bounded_map', 'map_as_completed', 'bounded_gather', 'BoundedTaskSet']

def __getattr__(name: str):
    if name == 'BoundedTaskSet':
        import warnings as _warnings
        _warnings.warn('BoundedTaskSet is deprecated. Use Python 3.11+ asyncio.TaskGroup instead. This module will be removed in a future sprint.', DeprecationWarning, stacklevel=2)
        return BoundedTaskSet
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')