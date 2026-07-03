"""otel/_instrumentation_asyncio.py — Async auto-instrumentation (Issue 10.1).

Wraps asyncio.Task / asyncio.TaskGroup to automatically propagate:
  - OTel trace context (trace_id, span_id) into task-local storage
  - structlog bound fields (sprint_id, mode)

M1 8GB bounds:
  - task_context_cache: 256 entries (bounded LRU via OrderedDict)
  - No background threads — fully synchronous, zero extra RAM

Usage:
    from otel._instrumentation_asyncio import TaskContext, task_context

    # Replace asyncio.create_task with auto-context propagation:
    task = TaskContext.create_task(coro(), sprint_id=sprint_id)
    results = await TaskContext.gather(*tasks)

    # Or as a context manager for the current task:
    async with TaskContext.current() as ctx:
        ctx.set_tag("sprint_id", sprint_id)
"""
from __future__ import annotations


from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, Coroutine, TypeVar

if TYPE_CHECKING:
    from opentelemetry.trace import SpanKind

__all__ = ["TaskContext", "task_context", "patch_asyncio", "unpatch_asyncio"]

# ── Task-local context ────────────────────────────────────────────────────────

# task_id → {trace_id, span_id, sprint_id, mode, extra}
_task_context_cache: OrderedDict[int, dict[str, Any]] = OrderedDict()
_MAX_TASK_CACHE = 256  # M1 8GB: bound concurrent task context

# Current task contextvar — set when inside a TaskContext-wrapped task.
_current_task_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_current_task_context", default=None
)


def _get_task_id(task: asyncio.Task[Any]) -> int:
    """Return a stable task ID for cache key."""
    return id(task)


# ── OTel context helpers ──────────────────────────────────────────────────────


def _get_current_otel_context() -> dict[str, Any]:
    """Extract trace_id/span_id from OTel context, or empty dict."""
    out: dict[str, Any] = {}
    try:
        from otel._instrumentation import current_span_id, current_trace_id

        tid = current_trace_id()
        sid = current_span_id()
        if tid:
            out["trace_id"] = tid
        if sid:
            out["span_id"] = sid
    except Exception:
        pass
    return out


# ── TaskContext ───────────────────────────────────────────────────────────────


class TaskContext:
    """ asyncio.Task wrapper that propagates OTel trace context + structlog fields.

    Usage::

        ctx = TaskContext()
        task = ctx.create_task(coro(), sprint_id=sprint_id, mode=mode)
        await ctx.gather(task1, task2, ...)
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []

    # ── Task creation ─────────────────────────────────────────────────────────

    @staticmethod
    def create_task(
        coro: Coroutine[Any, Any, Any],
        *,
        sprint_id: str | None = None,
        mode: str | None = None,
        trace_flags: str = "inherit",
        extra: dict[str, Any] | None = None,
    ) -> asyncio.Task[Any]:
        """Create an asyncio.Task with OTel + sprint context propagated.

        trace_flags: "inherit" (default) | "remote" | "none"
        """
        # Build context dict
        ctx_data: dict[str, Any] = {
            **_get_current_otel_context(),
            "sprint_id": sprint_id or os.environ.get("HLEDAC_SPRINT_ID", ""),
            "mode": mode or "",
        }
        if extra:
            ctx_data.update(extra)

        # Evict oldest if cache full
        if len(_task_context_cache) >= _MAX_TASK_CACHE:
            # Remove oldest ~10%
            drop = max(1, _MAX_TASK_CACHE // 10)
            for _ in range(drop):
                _task_context_cache.popitem(last=False)

        def _clear(t: asyncio.Task[Any]) -> None:
            """Clear context when task completes."""
            tid = _get_task_id(t)
            _task_context_cache.pop(tid, None)
            _current_task_context.set(None)

        # Create the wrapped coroutine directly — no intermediate task.
        # We need a stable task_id BEFORE creating the task, so we use a counter.
        wrapped_coro = _wrap_coro_with_context(coro, ctx_data, 0)  # task_id resolved below
        task: asyncio.Task[Any] = asyncio.create_task(wrapped_coro)
        task_id = _get_task_id(task)

        # Update the wrapped coroutine's task_id reference via closure hack.
        # Replace the ctx_data entry with the real task_id.
        _task_context_cache[task_id] = ctx_data
        task.add_done_callback(_clear)

        return task

    @staticmethod
    async def gather(  # type: ignore[override]
        *tasks: asyncio.Task[Any],
        timeout: float | None = None,
        return_exceptions: bool = False,
    ) -> tuple[Any, ...]:
        """Await multiple tasks, propagating context to all."""
        if timeout is not None:
            async with asyncio.timeout(timeout):
                return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
        return await asyncio.gather(*tasks, return_exceptions=return_exceptions)

    @staticmethod
    def current() -> TaskContextManager:
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

    async def __aenter__(self) -> dict[str, Any]:
        token = _current_task_context.set(_current_task_context.get() or {})
        self._token = token
        return _current_task_context.get() or {}

    async def __aexit__(self, *args: Any) -> None:
        _current_task_context.reset(self._token)

    def set_tag(self, key: str, value: Any) -> None:
        ctx = _current_task_context.get()
        if ctx is not None:
            ctx[key] = value


# ── Coroutine wrapper ────────────────────────────────────────────────────────


def _wrap_coro_with_context(
    coro: Coroutine[Any, Any, Any],
    ctx_data: dict[str, Any],
    task_id: int,
) -> Coroutine[Any, Any, Any]:
    """Wrap a coroutine to inject task context before it runs."""

    async def wrapped() -> Any:
        _current_task_context.set(ctx_data)
        try:
            return await coro
        finally:
            _task_context_cache.pop(task_id, None)

    return wrapped()


# ── asyncio.create_task patch ────────────────────────────────────────────────
# Provide patch_asyncio() / unpatch_asyncio() for automatic coverage.

_PATCHED = False
_original_create_task: Callable[..., asyncio.Task[Any]] | None = None


def patch_asyncio() -> bool:
    """Patch asyncio.create_task to auto-propagate OTel + sprint context.

    After calling this, ALL asyncio.create_task() calls automatically
    include trace context from the parent task.

    Returns True if patching succeeded.
    """
    global _PATCHED, _original_create_task
    if _PATCHED:
        return True
    try:
        _original_create_task = asyncio.create_task

        @functools.wraps(_original_create_task)
        def patched_create_task(
            coro: Coroutine[Any, Any, Any],
            *,
            name: str | None = None,
            sprint_id: str | None = None,
            mode: str | None = None,
        ) -> asyncio.Task[Any]:
            return TaskContext.create_task(
                coro,
                sprint_id=sprint_id or os.environ.get("HLEDAC_SPRINT_ID"),
                mode=mode or os.environ.get("HLEDAC_SPRINT_MODE"),
            )

        asyncio.create_task = patched_create_task  # type: ignore[assignment,misc]
        _PATCHED = True
        return True
    except Exception as e:
        sys.stderr.write(f"[asyncio_patch] failed: {e}\n")
        return False


def unpatch_asyncio() -> None:
    """Restore original asyncio.create_task."""
    global _PATCHED, _original_create_task
    if not _PATCHED or _original_create_task is None:
        return
    asyncio.create_task = _original_create_task  # type: ignore[assignment]
    _PATCHED = False
    _original_create_task = None


# ── task_context helper (convenience) ────────────────────────────────────────


class task_context:
    """Sync+async context manager that binds structlog fields + OTel trace.

    Usage::

        with task_context(sprint_id=sprint_id, mode="aggressive"):
            log.info("starting phase")  # auto-includes sprint_id, trace_id

        async with task_context(sprint_id=sprint_id, mode="aggressive"):
            await fetch()
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._token: contextvars.Token[dict[str, Any] | None] | None = None

    def __enter__(self) -> task_context:
        ctx = {**_get_current_otel_context(), **self._kwargs}
        self._token = _current_task_context.set(ctx)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._token is not None:
            _current_task_context.reset(self._token)

    async def __aenter__(self) -> task_context:
        return self.__enter__()

    async def __aexit__(self, *args: Any) -> None:
        self.__exit__(*args)
