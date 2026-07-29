"""runtime/observability_async_handler.py — Async log handler pro M1 8GB.

Python 3.14 built-in logging.QueueHandler je základ, ale pro M1 8GB je lepší
vlastní implementace přes asyncio.Queue protože:
1. logging.QueueHandler blokuje při plné frontě (PUTaty logger thread)
2. Vlastní queue můžeme bounded — max 10_000 zpráv, pak drop oldest
3. Background thread jen flushuje do stdout/stderr

Bounded: MAX_QUEUE_SIZE=10_000, drop oldest na overflow.
M1 8GB safe: ~10_000 JSON log lines ≈ 2-5 MB RAM.

Env vars:
  HLEDAC_ASYNC_LOG=1 — enable async handler (default 0 = sync pro stabilitu)
  HLEDAC_ASYNC_LOG_DROP_OLDEST=1 — drop oldest on overflow (default 1)

Decoupling (F350M-R):
  AsyncLogHandler závisí na abstrakcích (protocols), ne na konkrétních třídách.
  Adaptéry: AsyncioQueueAdapter, AsyncioLockAdapter, ThreadingEventAdapter.
  Lze snadno vyměnit za jiné implementace (mock, fake, jiné frameworky).
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import queue
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from hledac.universal.utils.async_helpers import safe_create_task

_structlog: Any | None = None


def _get_structlog() -> Any | None:
    global _structlog
    if _structlog is None:
        try:
            import structlog as _structlog
        except ImportError:
            _structlog = None
    return _structlog


# === Protocol interfaces (Dependency Inversion) ===

@runtime_checkable
class AsyncQueue(Protocol):
    """Abstract async queue interface."""

    def put_nowait(self, item: Any) -> None:
        """Non-blocking put."""
        ...

    def get_nowait(self) -> Any:
        """Non-blocking get, raises QueueEmpty if empty."""
        ...

    async def join(self) -> None:
        """Block until all items are processed."""
        ...

    def task_done(self) -> None:
        """Mark item as processed."""
        ...


@runtime_checkable
class AsyncLock(Protocol):
    """Abstract async lock interface."""

    async def __aenter__(self) -> Any:
        ...

    async def __aexit__(self, *args: Any) -> None:
        ...


@runtime_checkable
class ThreadEvent(Protocol):
    """Abstract thread event interface."""

    def is_set(self) -> bool:
        """Check if event is set."""
        ...

    def set(self) -> None:
        """Set the event."""
        ...

    def clear(self) -> None:
        """Clear the event."""
        ...


# === Concrete adapters (default implementations) ===

class AsyncioQueueAdapter:
    """asyncio.Queue adapter implementing AsyncQueue protocol."""

    __slots__ = ("_queue",)

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)

    def put_nowait(self, item: Any) -> None:
        self._queue.put_nowait(item)

    def get_nowait(self) -> Any:
        return self._queue.get_nowait()

    async def join(self) -> None:
        await self._queue.join()

    def task_done(self) -> None:
        self._queue.task_done()

    @property
    def _internal_queue(self) -> asyncio.Queue[str]:
        return self._queue


class AsyncioLockAdapter:
    """asyncio.Lock adapter implementing AsyncLock protocol."""

    __slots__ = ("_lock",)

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()

    async def __aenter__(self) -> Any:
        await self._lock.acquire()
        return self._lock

    async def __aexit__(self, *_: Any) -> None:
        self._lock.release()

    @property
    def _internal_lock(self) -> asyncio.Lock:
        return self._lock


class ThreadingEventAdapter:
    """threading.Event adapter implementing ThreadEvent protocol."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event: threading.Event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    @property
    def _internal_event(self) -> threading.Event:
        return self._event


class StdQueueAdapter:
    """Standard library queue adapter (fallback for non-async contexts)."""

    __slots__ = ("_queue",)

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)

    def put_nowait(self, item: Any) -> None:
        self._queue.put_nowait(item)

    def get_nowait(self) -> Any:
        return self._queue.get_nowait()

    async def join(self) -> None:
        self._queue.join()

    def task_done(self) -> None:
        self._queue.task_done()


# === Configuration constants ===

MAX_QUEUE_SIZE = 10_000
_ASYNC_LOG_ENABLED = os.environ.get("HLEDAC_ASYNC_LOG", "0").strip() == "1"


# === AsyncLogHandler with Dependency Injection ===

class AsyncLogHandler:
    """
    Async-safe log handler přes asyncio.Queue + background thread.

    Flow:
      logger.info(...) → queue.put_nowait() → background thread → stdout/stderr

    Bounded: MAX_QUEUE_SIZE, drop oldest on overflow (configurable).
    Fail-safe: any error silently drops the message.

    Decoupled dependencies via protocols:
      - _queue: AsyncQueue (default: AsyncioQueueAdapter)
      - _lock: AsyncLock (default: AsyncioLockAdapter)
      - _stop_event: ThreadEvent (default: ThreadingEventAdapter)
    """
    _instance: "AsyncLogHandler | None" = None
    _lock: AsyncLock | None = None

    __slots__ = tuple(
        ("_drop_oldest", "_queue", "_started", "_stop_event", "_thread")
    )

    def __init__(
        self,
        queue: AsyncQueue | None = None,
        stop_event: ThreadEvent | None = None,
        drop_oldest: bool = True,
        queue_size: int = MAX_QUEUE_SIZE,
    ) -> None:
        self._drop_oldest = drop_oldest
        self._queue: AsyncQueue = queue or AsyncioQueueAdapter(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._stop_event: ThreadEvent = stop_event or ThreadingEventAdapter()
        self._started = False

    @classmethod
    async def get_instance(cls) -> "AsyncLogHandler":
        """Get or create singleton instance (async-safe)."""
        if cls._lock is None:
            cls._lock = AsyncioLockAdapter()
        async with cls._lock:
            if cls._instance is None:
                drop_oldest_env = (
                    os.environ.get("HLEDAC_ASYNC_LOG_DROP_OLDEST", "1").strip()
                    != "0"
                )
                cls._instance = cls(drop_oldest=drop_oldest_env)
        return cls._instance

    async def start(self) -> None:
        """Start background flush thread."""
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="AsyncLogHandler-flush"
        )
        self._thread.start()

    def _flush_loop(self) -> None:
        """Background thread: flush queue to stdout/stderr."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get_nowait()
                if msg is None:
                    continue
                # Detect JSON by first char - JSON lines always start with { or [
                if msg.startswith("{") or msg.startswith("["):
                    sys.stdout.write(msg + "\n")
                else:
                    sys.stderr.write(msg + "\n")
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                pass

    async def emit(self, message: str) -> None:
        """
        Emit a log message asynchronously.

        If queue is full: drop oldest (if drop_oldest=True) or drop newest.
        """
        try:
            if self._drop_oldest:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            pass
        except Exception:
            pass

    async def stop(self) -> None:
        """Stop the flush thread gracefully."""
        if not self._started:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._started = False


async def configure_async_logging() -> None:
    """Configure async logging handler for the current event loop."""
    if not _ASYNC_LOG_ENABLED:
        return

    handler = await AsyncLogHandler.get_instance()
    await handler.start()

    sl = _get_structlog()
    if sl is not None:
        try:
            sl.configure(
                processors=[
                    sl.contextvars.merge_contextvars,
                    sl.stdlib.filter_by_level,
                    sl.stdlib.add_log_level,
                    sl.stdlib.PositionalArgumentsFormatter(),
                    _inject_trace_context_async,
                    _json_renderer_async,
                ],
                wrapper_class=sl.stdlib.BoundLogger,
                context_class=dict,
                logger_factory=sl.stdlib.LoggerFactory(),
                cache_logger_on_first_use=True,
            )
        except Exception:
            pass


def _inject_trace_context_async(
    _logger: Any, _method_name: str, event: dict[str, Any]
) -> dict[str, Any]:
    """Async-aware structlog processor: inject trace context into event_dict.

    structlog processor protocol: (logger, method_name, event_dict) -> event_dict
    """
    out: dict[str, Any] = {}
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        if span is not None:
            ctx = span.get_span_context()
            if ctx.is_valid:
                out["trace_id"] = format(ctx.trace_id, "032x")
                out["span_id"] = format(ctx.span_id, "016x")
    except Exception:
        pass

    if out:
        event.update(out)
    return event


def _json_renderer_async(
    _logger: Any, _method: str, event: dict[str, Any]
) -> str:
    """Async-safe structlog JSON renderer that uses async queue.

    structlog processor protocol: (logger, method_name, event_dict) -> str

    Falls back to sync write if async handler not available or no event loop.
    """
    try:
        import msgspec.json as _json

        ctx = _get_trace_context_for_renderer()
        if ctx:
            event.update(ctx)

        rendered = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": _method.upper(),
            "event": event.pop("event", ""),
            **event,
        }

        line = _json.encode(rendered)[:8192].decode("utf-8", errors="replace")

        if _ASYNC_LOG_ENABLED and AsyncLogHandler._instance is not None:
            try:
                safe_create_task(
                    AsyncLogHandler._instance.emit(line), name="log:emit"
                )
                return ""  # structlog requires non-None return
            except RuntimeError:
                # No event loop - fall through to sync write
                pass

        # Sync fallback
        out = (
            sys.stderr
            if _method.upper() in ("ERROR", "CRITICAL", "WARNING")
            else sys.stdout
        )
        out.write(line + "\n")
        return ""  # structlog requires non-None return
    except Exception:
        try:
            fallback = f"[{_method.upper()}] {event}"
            sys.stderr.write(fallback + "\n")
        except Exception:
            pass
        return ""


def _get_trace_context_for_renderer() -> dict[str, Any]:
    """Extract trace context for the async renderer (called in thread)."""
    out: dict[str, Any] = {}
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        if span is not None:
            ctx = span.get_span_context()
            if ctx.is_valid:
                out["trace_id"] = format(ctx.trace_id, "032x")
                out["span_id"] = format(ctx.span_id, "016x")
    except Exception:
        pass
    return out
