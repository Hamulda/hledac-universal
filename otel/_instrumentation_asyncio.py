"""otel/_instrumentation_asyncio.py — Async auto-instrumentation.

Wraps asyncio.Task to automatically propagate OTel trace context
(trace_id, span_id) into task-local storage via contextvars.

M1 8GB bounds:
  - task_context_cache: 512 entries (LRU via cachetools.LRUCache)
  - No background threads — zero extra RAM

Usage (canonical path):
    from utils.async_helpers import safe_create_task
    task = safe_create_task(coro(), name="fetch")

    # Inside the coroutine, read parent trace context:
    from utils.async_helpers import current_otel_context
    ctx = current_otel_context()  # {trace_id, span_id} or None
"""
from __future__ import annotations
import asyncio
import contextvars
import sys
from typing import TYPE_CHECKING, Any, Coroutine, TypeVar

try:
    from cachetools import LRUCache as _LRUCache
    _LRU_AVAILABLE = True
except ImportError:
    _LRU_AVAILABLE = False

if TYPE_CHECKING:
    pass

__all__ = ['TaskContext', 'task_context', 'current_otel_context', 'create_task_with_context']

# P0-3: cachetools LRUCache replaces hand-rolled OrderedDict LRU.
# _MAX_TASK_CACHE raised from 256 → 512 for better hit rate.
_MAX_TASK_CACHE = 512

# Cached version/uvloop checks — computed once at module load, not per call.
_PY_312_PLUS: bool = sys.version_info >= (3, 12)
_UVLOOP_INSTALLED: bool = sys.modules.get('uvloop') is not None
_EAGER_START_SUPPORTED: bool = _PY_312_PLUS and (not _UVLOOP_INSTALLED)

if _LRU_AVAILABLE:
    _task_context_cache = _LRUCache(maxsize=_MAX_TASK_CACHE)  # type: ignore[assignment, misc]
else:
    from collections import OrderedDict
    _task_context_cache: Any = OrderedDict()
_current_task_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar('_current_task_context', default=None)

def _get_current_otel_context() -> dict[str, Any]:
    """Extract trace_id/span_id from OTel context, or empty dict."""
    out: dict[str, Any] = {}
    try:
        from otel._instrumentation import current_span_id, current_trace_id
        tid = current_trace_id()
        sid = current_span_id()
        if tid:
            out['trace_id'] = tid
        if sid:
            out['span_id'] = sid
    except Exception:
        pass
    return out

def current_otel_context() -> dict[str, Any] | None:
    """
    Return the OTel trace context dict captured by safe_create_task for this task.

    Call this inside a coroutine spawned via safe_create_task (or
    create_task_with_context) to read the trace_id / span_id of the parent
    that created this task.

    Returns None if the coroutine was not created via safe_create_task or if
    OTel tracing is not active.
    """
    return _current_task_context.get()

def create_task_with_context(coro: Any, *, name: str | None=None, eager_start: bool=False, otel_trace: bool=True) -> asyncio.Task[Any]:
    """
    Create an asyncio.Task with OTel trace context propagation.

    This is the canonical task-creation path used by utils/async_helpers.
    safe_create_task, which is called from ~15+ sites across the codebase.

    The function:
      1. Captures current OTel trace context (trace_id / span_id)
      2. Wraps the coroutine so context is restored before execution
      3. Registers a done-callback to clean up the task context cache

    Args:
        coro:        The coroutine to wrap.
        name:        Optional task name.
        eager_start: Pass eager_start=True to asyncio.create_task (3.12+).
        otel_trace:  Capture and propagate OTel trace context (default True).

    Returns:
        asyncio.Task wrapping the coroutine.
    """
    captured: dict[str, Any] = {}
    if otel_trace:
        captured = _get_current_otel_context()

    async def _otel_wrapped() -> Any:
        """Inner coroutine: restore OTel context then run original."""
        _current_task_context.set(captured if captured else None)
        try:
            return await coro
        finally:
            _current_task_context.set(None)
    if eager_start and _EAGER_START_SUPPORTED:
        try:
            task: asyncio.Task[Any] = asyncio.create_task(_otel_wrapped(), name=name, eager_start=True)
        except TypeError:
            task = asyncio.create_task(_otel_wrapped(), name=name)
    else:
        task = asyncio.create_task(_otel_wrapped(), name=name)
    task_id = id(task)
    _task_context_cache[task_id] = captured if captured else {}

    def _clear(t: asyncio.Task[Any]) -> None:
        _task_context_cache.pop(id(t), None)
        _current_task_context.set(None)
    task.add_done_callback(_clear)
    return task

class TaskContext:
    """High-level asyncio.Task wrapper that propagates OTel + sprint context.

    Usage::

        task = TaskContext.create_task(coro(), sprint_id=sprint_id, mode=mode)
        await TaskContext.gather(task1, task2)

    For the canonical path used by the codebase, prefer safe_create_task().
    TaskContext is useful when you need sprint_id / mode tags.
    """
    __slots__ = tuple(('_tasks',))

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []

    @staticmethod
    def create_task(coro: Coroutine[Any, Any, Any], *, sprint_id: str | None=None, mode: str | None=None, extra: dict[str, Any] | None=None) -> asyncio.Task[Any]:
        """Create an asyncio.Task with OTel + sprint context propagated."""
        import os
        ctx_data: dict[str, Any] = {**_get_current_otel_context(), 'sprint_id': sprint_id or os.environ.get('HLEDAC_SPRINT_ID', ''), 'mode': mode or ''}
        if extra:
            ctx_data.update(extra)
        # P0-3: LRUCache handles eviction automatically; OrderedDict fallback
        # relies on manual popitem() above. No manual eviction needed for either.

        async def _wrapped() -> Any:
            _current_task_context.set(ctx_data)
            try:
                return await coro
            finally:
                _current_task_context.set(None)
        task: asyncio.Task[Any] = asyncio.create_task(_wrapped())
        task_id = id(task)
        _task_context_cache[task_id] = ctx_data

        def _clear(t: asyncio.Task[Any]) -> None:
            _task_context_cache.pop(id(t), None)
            _current_task_context.set(None)
        task.add_done_callback(_clear)
        return task

    @staticmethod
    async def gather(*tasks: asyncio.Task[Any], timeout: float | None=None, return_exceptions: bool=False) -> list[Any]:
        """Await multiple tasks, propagating context to all."""
        if timeout is not None:
            async with asyncio.timeout(timeout):
                return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
        return await asyncio.gather(*tasks, return_exceptions=return_exceptions)

    @staticmethod
    def current() -> 'TaskContextManager':
        """Return a context manager for the current task's context."""
        return TaskContextManager()

    @staticmethod
    def get() -> dict[str, Any] | None:
        """Return the current task's context dict, or None."""
        return _current_task_context.get()

    @staticmethod
    def set_tag(key: str, value: Any) -> None:
        """Set a tag on the current task's context."""
        ctx = _current_task_context.get()
        if ctx is not None:
            ctx[key] = value

class TaskContextManager:
    """Async context manager that exposes the current task's context."""
    __slots__ = ('_token',)

    def __init__(self) -> None:
        self._token: contextvars.Token[dict[str, Any] | None] | None = None

    async def __aenter__(self) -> dict[str, Any]:
        self._token = _current_task_context.set(_current_task_context.get() or {})
        return _current_task_context.get() or {}

    async def __aexit__(self, *_: Any) -> None:
        if self._token is not None:
            _current_task_context.reset(self._token)

    def set_tag(self, key: str, value: Any) -> None:
        ctx = _current_task_context.get()
        if ctx is not None:
            ctx[key] = value

class task_context:
    """Sync+async context manager that binds structlog fields + OTel trace.

    Usage::

        with task_context(sprint_id=sprint_id, mode="aggressive"):
            log.info("starting phase")  # auto-includes sprint_id, trace_id

        async with task_context(sprint_id=sprint_id, mode="aggressive"):
            await fetch()
    """
    __slots__ = ('_kwargs', '_token')

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._token: contextvars.Token[dict[str, Any] | None] | None = None

    def __enter__(self) -> task_context:
        ctx = {**_get_current_otel_context(), **self._kwargs}
        self._token = _current_task_context.set(ctx)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._token is not None:
            _current_task_context.reset(self._token)

    async def __aenter__(self) -> task_context:
        return self.__enter__()

    async def __aexit__(self, *args: Any) -> None:
        self.__exit__(*args)